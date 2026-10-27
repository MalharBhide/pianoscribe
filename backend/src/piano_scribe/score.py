"""Canonical musical representation.

One Score drives the SVG score, piano playback, editing, and all exports.
Performance timing (seconds) is stored separately from notation quantization
(beats), so readable notation never silently changes audible rhythm.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


def new_id(prefix: str = "n") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class Note:
    id: str
    pitch: int  # MIDI 0-127
    start_sec: float  # performance timing (audible)
    end_sec: float
    velocity: int = 80
    hand: str = "R"  # 'L' | 'R'
    role: str = "unknown"  # melody|bass|chord|inner|unknown
    provenance: str = "detected"  # detected|arranged|user-edited
    beat_onset: Optional[float] = None  # notation position (quantized)
    beat_dur: Optional[float] = None  # notated duration in beats
    finger: Optional[int] = None  # suggested fingering 1-5 (None = none)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def dur_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Note":
        return Note(**{k: d.get(k, v) for k, v in _NOTE_DEFAULTS.items()})


_NOTE_DEFAULTS = {
    "id": "", "pitch": 60, "start_sec": 0.0, "end_sec": 0.0, "velocity": 80,
    "hand": "R", "role": "unknown", "provenance": "detected",
    "beat_onset": None, "beat_dur": None, "finger": None, "evidence": {},
}


@dataclass
class TempoMap:
    beats: List[float] = field(default_factory=list)  # beat times in seconds
    bpm: float = 120.0
    beats_per_bar: int = 4

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "TempoMap":
        return TempoMap(
            beats=list(d.get("beats", [])),
            bpm=float(d.get("bpm", 120.0)),
            beats_per_bar=int(d.get("beats_per_bar", 4)),
        )


@dataclass
class Score:
    notes: List[Note] = field(default_factory=list)
    tempo: TempoMap = field(default_factory=TempoMap)
    time_sig: str = "4/4"
    title: str = "Untitled"
    mode: str = "piano"  # piano | arrangement
    chords: List[Dict[str, Any]] = field(default_factory=list)
    raw_detections: List[Dict[str, Any]] = field(default_factory=list)
    processing: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)  # undo stack
    redo_stack: List[Dict[str, Any]] = field(default_factory=list)
    version: int = 1
    key_fifths: int = 0  # key signature: sharps>0 / flats<0, circle of fifths

    # -- serialization -----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "notes": [n.to_dict() for n in self.notes],
            "tempo": self.tempo.to_dict(),
            "time_sig": self.time_sig,
            "title": self.title,
            "mode": self.mode,
            "chords": self.chords,
            "raw_detections": self.raw_detections,
            "processing": self.processing,
            "history": self.history[-50:],
            "redo_stack": self.redo_stack[-50:],
            "version": self.version,
            "key_fifths": self.key_fifths,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Score":
        s = Score()
        s.notes = [Note.from_dict(x) for x in d.get("notes", [])]
        s.tempo = TempoMap.from_dict(d.get("tempo", {}))
        s.time_sig = d.get("time_sig", "4/4")
        s.title = d.get("title", "Untitled")
        s.mode = d.get("mode", "piano")
        s.chords = list(d.get("chords", []))
        s.raw_detections = list(d.get("raw_detections", []))
        s.processing = dict(d.get("processing", {}))
        s.history = list(d.get("history", []))
        s.redo_stack = list(d.get("redo_stack", []))
        s.version = int(d.get("version", 1))
        s.key_fifths = int(d.get("key_fifths", 0))
        return s

    # -- editing (all edits push history; raw_detections never touched) ----
    def _snapshot(self) -> Dict[str, Any]:
        return {"notes": [n.to_dict() for n in self.notes], "version": self.version}

    def _commit(self, label: str, pre: Dict[str, Any]) -> None:
        self.history.append(
            {"label": label, "before": pre, "after": self._snapshot()}
        )
        self.history = self.history[-100:]
        self.redo_stack.clear()
        self.version += 1

    def _restore(self, snap: Dict[str, Any]) -> None:
        self.notes = [Note.from_dict(x) for x in snap["notes"]]

    def undo(self) -> bool:
        if not self.history:
            return False
        entry = self.history.pop()
        self.redo_stack.append(entry)
        self._restore(entry["before"])
        self.version += 1
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        entry = self.redo_stack.pop()
        self.history.append(entry)
        self._restore(entry["after"])
        self.version += 1
        return True

    def get(self, note_id: str) -> Optional[Note]:
        for n in self.notes:
            if n.id == note_id:
                return n
        return None

    def update_note(self, note_id: str, patch: Dict[str, Any], label: str = "edit") -> Optional[Note]:
        n = self.get(note_id)
        if n is None:
            return None
        pre = self._snapshot()
        for k in ("pitch", "start_sec", "end_sec", "velocity", "hand", "role", "finger"):
            if k in patch:
                setattr(n, k, patch[k])
        n.pitch = max(0, min(127, int(n.pitch)))
        n.velocity = max(1, min(127, int(n.velocity)))
        if n.end_sec < n.start_sec:
            n.end_sec = n.start_sec + 0.1
        n.provenance = "user-edited"
        self._commit(label, pre)
        return n

    def add_note(self, patch: Dict[str, Any]) -> Note:
        n = Note(
            id=patch.get("id") or new_id(),
            pitch=int(patch.get("pitch", 60)),
            start_sec=float(patch.get("start_sec", 0.0)),
            end_sec=float(patch.get("end_sec", 0.5)),
            velocity=int(patch.get("velocity", 80)),
            hand=str(patch.get("hand", "R")),
            role=str(patch.get("role", "unknown")),
            provenance="user-edited",
            beat_onset=patch.get("beat_onset"),
            beat_dur=patch.get("beat_dur"),
            evidence={},
        )
        pre = self._snapshot()
        self.notes.append(n)
        self._commit("add note", pre)
        return n

    def delete_notes(self, ids: List[str]) -> int:
        pre = self._snapshot()
        before = len(self.notes)
        keep = {n.id for n in self.notes} - set(ids)
        self.notes = [n for n in self.notes if n.id in keep]
        self._commit("delete notes", pre)
        return before - len(self.notes)

    def transpose(self, ids: List[str], semitones: int) -> int:
        pre = self._snapshot()
        count = 0
        for n in self.notes:
            if n.id in ids:
                n.pitch = max(0, min(127, n.pitch + semitones))
                n.provenance = "user-edited"
                count += 1
        if count:
            self._commit(f"transpose {semitones:+d}", pre)
        return count

    def snapshot_copy(self) -> Dict[str, Any]:
        return copy.deepcopy(self._snapshot())
