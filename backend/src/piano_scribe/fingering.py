"""Suggested piano fingering (heuristic, a starting point — not a teacher).

Greedy cost-based assignment per hand: prefers small moves, changes fingers
on repeated notes, walks fingers with melodic direction, and spreads chord
tones across the hand. Chords and fast passagework are approximated.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .score import Note, Score


def suggest_fingering(score: Score) -> int:
    count = 0
    for hand in ("R", "L"):
        count += _finger_hand([n for n in score.notes if n.hand == hand], hand)
    return count


def _finger_hand(notes: List[Note], hand: str) -> int:
    notes.sort(key=lambda n: (n.start_sec, n.pitch))
    # Group simultaneous onsets into chords.
    groups: List[List[Note]] = []
    for n in notes:
        if groups and n.start_sec - groups[-1][0].start_sec <= 0.03:
            groups[-1].append(n)
        else:
            groups.append([n])
    pos: Dict[int, int] = {}  # finger -> last pitch
    last_finger = 3
    last_pitch: Optional[int] = None
    count = 0
    asc = hand == "R"  # RH: pitch up -> finger up; LH mirrored
    for g in groups:
        if len(g) == 1:
            n = g[0]
            if not pos:
                f = 1 if asc else 5  # phrase start: thumb (RH) / pinky (LH)
            else:
                f = _pick_single(n.pitch, pos, last_finger, last_pitch, asc)
            n.finger = f
            pos[f] = n.pitch
            last_finger, last_pitch = f, n.pitch
            count += 1
            continue
        ordered = sorted(g, key=lambda n: n.pitch)
        fingers = _chord_fingers(len(ordered), hand)
        for n, f in zip(ordered, fingers):
            n.finger = f
            pos[f] = n.pitch
            count += 1
        last_finger = fingers[-1] if asc else fingers[0]
        last_pitch = ordered[-1].pitch if asc else ordered[0].pitch
    return count


def _pick_single(pitch: int, pos: Dict[int, int], last_finger: int,
                 last_pitch: Optional[int], asc: bool) -> int:
    best_f, best_cost = 3, float("inf")
    for f in (1, 2, 3, 4, 5):
        cost = abs(pitch - pos[f]) if f in pos else 4.0
        if last_pitch is not None:
            if pitch == last_pitch:
                # Repeated note: any finger that already played it is out.
                if f in pos and pos[f] == pitch:
                    cost += 30
            else:
                if f == last_finger:
                    cost += 24  # avoid reusing a finger on a new pitch
                going_up = pitch > last_pitch
                step = 1 if going_up else -1
                if not asc:
                    step = -step
                if f == last_finger + step:
                    cost -= 2  # walk to the neighboring finger
                if asc == going_up and f < last_finger:
                    cost += 6
                if asc != going_up and f > last_finger:
                    cost += 6
        # Gentle preference for middle fingers on first notes.
        cost += abs(f - 3) * 0.5
        if cost < best_cost:
            best_f, best_cost = f, cost
    return best_f


def _chord_fingers(n: int, hand: str) -> List[int]:
    if n <= 1:
        return [3]
    if n == 2:
        return [1, 5] if hand == "R" else [5, 1]
    if n == 3:
        return [1, 3, 5] if hand == "R" else [5, 3, 1]
    if n == 4:
        return [1, 2, 3, 5] if hand == "R" else [5, 3, 2, 1]
    if n == 5:
        seq = [1, 2, 3, 4, 5]
        return seq if hand == "R" else list(reversed(seq))
    # Wider than a hand: edge fingers double (approximation).
    seq = [1, 2, 3, 4, 5] + [5] * (n - 5)
    return seq if hand == "R" else list(reversed(seq))
