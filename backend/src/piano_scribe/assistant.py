"""Score assistant: constrained commands over stable IDs.

Rule-based in the slice; an LLM backend (e.g. Meta models) may produce the same
validated action objects later. The assistant never edits silently: it proposes
structured actions, app code validates, UI previews, score history gives undo.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .notation import NAMES, measure_bounds
from .score import Score

CHORD_NAMES = {0: "C", 1: "Db", 2: "D", 3: "Eb", 4: "E", 5: "F",
               6: "Gb", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B"}


def _notes_in_measures(score: Score, m0: int, m1: int, hand: Optional[str] = None):
    end_sec = max([n.end_sec for n in score.notes], default=1.0)
    bounds = measure_bounds(score.tempo, end_sec)
    out = []
    for m in range(max(0, m0 - 1), min(len(bounds), m1)):
        ms, me = bounds[m]
        for n in score.notes:
            if ms - 1e-6 <= n.start_sec < me - 1e-6 and (hand is None or n.hand == hand):
                out.append(n)
    return out, len(bounds)


def describe_chord(score: Score, measure: int) -> str:
    notes, n_bars = _notes_in_measures(score, measure, measure)
    if measure < 1 or measure > n_bars:
        return f"Measure {measure} is out of range (score has {n_bars} measures)."
    if not notes:
        return f"Measure {measure} has no notes (a rest)."
    pcs = sorted({n.pitch % 12 for n in notes})
    bass = min(notes, key=lambda n: n.pitch)
    names = ", ".join(f"{NAMES[n.pitch % 12]}{n.pitch // 12 - 1}" for n in sorted(notes, key=lambda n: n.pitch)[:8])
    guess = _guess_quality(pcs)
    return (f"Measure {measure}: {len(notes)} notes ({names}). "
            f"Bass {NAMES[bass.pitch % 12]}. Pitch classes {', '.join(CHORD_NAMES[p] for p in pcs)}; "
            f"closest reading: {guess}. This is a description of the current score, not a verdict on accuracy.")


def _guess_quality(pcs: List[int]) -> str:
    s = set(pcs)
    for root in s:
        iv = sorted(((p - root) % 12) for p in s)
        if iv == [0, 4, 7]:
            return f"{CHORD_NAMES[root]} major"
        if iv == [0, 3, 7]:
            return f"{CHORD_NAMES[root]} minor"
        if iv == [0, 4, 7, 11]:
            return f"{CHORD_NAMES[root]} major 7"
        if iv == [0, 4, 7, 10]:
            return f"{CHORD_NAMES[root]} 7"
        if iv == [0, 3, 7, 10]:
            return f"{CHORD_NAMES[root]} minor 7"
        if iv == [0, 3, 6]:
            return f"{CHORD_NAMES[root]} diminished"
    return "no standard triad (cluster or partial voicing)"


def parse_command(text: str, score: Score, selection: List[str]) -> Dict[str, Any]:
    t = text.strip().lower()
    m = re.search(r"chord in measure (\d+)", t)
    if m or "explain" in t and "measure" in t:
        num = int(m.group(1)) if m else (int(re.search(r"measure (\d+)", t).group(1)) if re.search(r"measure (\d+)", t) else 1)  # noqa
        return {"reply": describe_chord(score, num), "actions": []}
    m = re.search(r"transpose.*?(-?\d+)\s*semitone", t) or re.search(r"transpose.*?down.*?(\d+)", t) or re.search(r"transpose.*?up.*?(\d+)", t)
    if "transpose" in t and m:
        semis = int(m.group(1))
        if "down" in t:
            semis = -abs(semis)
        elif "up" in t:
            semis = abs(semis)
        ids = [n.id for n in score.notes if n.id in selection]
        if not ids:
            return {"reply": "Select one or more notes first, then ask me to transpose them.", "actions": []}
        if abs(semis) > 24:
            return {"reply": "Transpose range is limited to ±24 semitones.", "actions": []}
        return {"reply": f"Ready to transpose {len(ids)} note(s) by {semis:+d} semitones. Preview and confirm to apply (undo available).",
                "actions": [{"type": "transpose", "ids": ids, "semitones": semis}]}
    m = re.search(r"left hand.*?measures? (\d+)\s*[–\-to ]+\s*(\d+).*simpl", t) or \
        re.search(r"simpl.*left hand.*?measures? (\d+)\s*[–\-to ]+\s*(\d+)", t)
    if m and "simpl" in t:
        m0, m1 = int(m.group(1)), int(m.group(2))
        notes, n_bars = _notes_in_measures(score, m0, m1, hand="L")
        if m0 > n_bars or m1 < 1:
            return {"reply": f"Measures {m0}–{m1} are out of range (score has {n_bars} measures).", "actions": []}
        keep = _simplify_keep(score, m0, m1)
        drop = len(notes) - len(keep)
        return {"reply": f"Measures {m0}–{m1} left hand: keep the lowest note per measure ({len(keep)} notes), "
                         f"remove {max(0, drop)} accompaniment tones. Preview and confirm to apply (undo available).",
                "actions": [{"type": "simplify_lh", "m0": m0, "m1": m1,
                             "keep_ids": [n.id for n in keep],
                             "drop_ids": [n.id for n in notes if n.id not in {k.id for k in keep}]}]}
    m = re.search(r"loop.*?(\d+)\s*%|(\d+)\s*%.*?speed", t)
    if "loop" in t:
        pct = int(m.group(1) or m.group(2)) if m else 75
        pct = max(25, min(100, pct))
        return {"reply": f"Looping the selection at {pct}% speed. Press play to hear it.",
                "actions": [{"type": "loop", "speed": pct / 100.0}]}
    return {"reply": ("I can: explain the chord in measure N · transpose the selection (±N semitones) · "
                      "simplify the left hand in measures A–B · loop the selection at N% speed. "
                      "I only change the score after you confirm, and every change can be undone."),
            "actions": []}


def _simplify_keep(score: Score, m0: int, m1: int):
    end_sec = max([n.end_sec for n in score.notes], default=1.0)
    bounds = measure_bounds(score.tempo, end_sec)
    keep = []
    for m in range(max(0, m0 - 1), min(len(bounds), m1)):
        ms, me = bounds[m]
        cands = [n for n in score.notes if n.hand == "L" and ms - 1e-6 <= n.start_sec < me - 1e-6]
        if cands:
            keep.append(min(cands, key=lambda n: (n.pitch, n.start_sec)))
    return keep


def apply_action(score: Score, action: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + apply. Returns {ok, message, ...}."""
    atype = action.get("type")
    if atype == "transpose":
        ids = [i for i in action.get("ids", []) if score.get(i)]
        semis = int(action.get("semitones", 0))
        if not ids or not (-24 <= semis <= 24):
            return {"ok": False, "message": "Invalid transpose action."}
        n = score.transpose(ids, semis)
        return {"ok": True, "message": f"Transposed {n} note(s) by {semis:+d} semitones.", "affected": ids}
    if atype == "simplify_lh":
        drop = [i for i in action.get("drop_ids", []) if score.get(i)]
        # Safety: only delete LH notes, never melody-role notes.
        drop = [i for i in drop if score.get(i).hand == "L" and score.get(i).role != "melody"]
        if not drop:
            return {"ok": False, "message": "Nothing safe to simplify (melody notes are protected)."}
        score.delete_notes(drop)
        return {"ok": True, "message": f"Simplified left hand: removed {len(drop)} tone(s).", "affected": drop}
    return {"ok": False, "message": f"Unknown action '{atype}'."}
