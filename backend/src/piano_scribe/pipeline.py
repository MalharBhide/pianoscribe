"""Pipeline: ingest -> transcribe (+arrange) -> quantize -> Score + artifacts."""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from . import PROCESSING_VERSION
from .audio_ingest import AudioError, iter_windows, load_audio, merge_window_notes
from .audio_models import REGISTRY
from .config import get_limits
from .notation import assign_hands_piano, quantize
from .score import Note, Score, TempoMap, new_id


def transcribe_signal(mono: np.ndarray, sr: int, mode: str, density: str,
                      progress: Optional[Callable[[float, str], None]] = None,
                      window_sec: float = 30.0, overlap_sec: float = 5.0,
                      evidence_mode: bool = False) -> Dict[str, Any]:
    """Run a transcriber over windows; merge to absolute timeline.

    evidence_mode: run the piano detector as the raw-evidence layer even in
    arrangement mode (its output is stored, not shown as the score).
    """
    key = "piano" if (mode == "piano" or evidence_mode) else "arrangement"
    tx = REGISTRY[key]
    limits_cfg = {"density": density}
    dur = len(mono) / float(sr)
    if progress:
        progress(0.02, "decode")
    if dur <= window_sec * 1.2:
        res = tx.transcribe(mono, sr, progress=lambda f, s: progress(0.05 + 0.8 * f, s) if progress else None,
                            config=limits_cfg)
        return {"notes": res.notes, "beats": res.beats, "bpm": res.bpm,
                "chords": res.chords, "meta": res.meta, "windows": 1}
    wins = list(iter_windows(len(mono), window_sec, overlap_sec, sr))
    per_win: List[Any] = []
    all_beats: List[float] = []
    all_chords: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}
    for i, w in enumerate(wins):
        sl = mono[w.start_sample:w.end_sample]
        if progress:
            progress(0.05 + 0.8 * i / len(wins), f"transcribe window {i + 1}/{len(wins)}")
        res = tx.transcribe(sl, sr, config=limits_cfg)
        per_win.append((w, res.notes))
        off = w.start_sample / float(sr)
        core_lo = off + (overlap_sec if i > 0 else 0.0)
        for b in res.beats:
            if b + off >= core_lo:
                all_beats.append(b + off)
        for ch in res.chords:
            c2 = dict(ch)
            c2["start_sec"] += off
            c2["end_sec"] += off
            if c2["start_sec"] >= core_lo:
                all_chords.append(c2)
        meta = res.meta
    notes = merge_window_notes(per_win, sr, overlap_sec)
    all_beats.sort()
    # dedup beats closer than 150 ms (overlap doubles)
    dedup: List[float] = []
    for b in all_beats:
        if not dedup or b - dedup[-1] > 0.15:
            dedup.append(b)
    return {"notes": notes, "beats": dedup, "bpm": meta.get("config", {}).get("bpm", 120.0)
            if isinstance(meta.get("config"), dict) else 120.0,
            "chords": all_chords, "meta": meta, "windows": len(wins)}


def build_score(audio_path: str, mode: str, density: str, title: str,
                progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
    """Full pipeline for one upload. Returns {score, audio_info, timing}."""
    t0 = time.time()
    lim = get_limits()
    mono, info = load_audio(audio_path, target_sr=lim.target_sr,
                            max_duration_sec=lim.max_duration_sec)
    if mode not in ("piano", "arrangement"):
        raise AudioError(f"unknown mode '{mode}'")
    main = transcribe_signal(mono, lim.target_sr, mode, density, progress,
                             lim.window_sec, lim.overlap_sec)
    raw: List[Dict[str, Any]] = list(main["notes"])
    if mode == "arrangement":
        # Raw detected evidence layer stays separate from arrangement choices.
        ev = transcribe_signal(mono, lim.target_sr, mode, density, None,
                               lim.window_sec, lim.overlap_sec, evidence_mode=True)
        raw = ev["notes"]
        for r in raw:
            r["provenance"] = "detected"
    if progress:
        progress(0.9, "notation")
    bpm = main.get("bpm") or 120.0
    tempo = TempoMap(beats=[round(b, 4) for b in main["beats"]], bpm=float(bpm), beats_per_bar=4)
    notes: List[Note] = []
    for d in main["notes"]:
        notes.append(Note(
            id=new_id(), pitch=int(max(0, min(127, round(d["pitch"])))),
            start_sec=float(d["start_sec"]), end_sec=float(max(d["start_sec"] + 0.03, d["end_sec"])),
            velocity=int(d.get("velocity", 80)),
            hand=str(d.get("hand", "R")), role=str(d.get("role", "unknown")),
            provenance=str(d.get("provenance", "detected" if mode == "piano" else "arranged")),
            evidence=dict(d.get("evidence", {})),
        ))
    score = Score(notes=notes, tempo=tempo, title=title or os.path.basename(audio_path), mode=mode)
    score.raw_detections = [
        {"pitch": int(d["pitch"]), "start_sec": round(float(d["start_sec"]), 4),
         "end_sec": round(float(d["end_sec"]), 4), "velocity": int(d.get("velocity", 80))}
        for d in raw
    ]
    score.processing = {
        **main["meta"],
        "mode": mode, "density": density, "audio": info.to_dict(),
        "windows": main["windows"], "processing_version": PROCESSING_VERSION,
    }
    if mode == "piano":
        assign_hands_piano(score)
    quantize(score)
    timing = {"total_sec": round(time.time() - t0, 2),
              "audio_sec": round(info.duration_sec, 2)}
    if progress:
        progress(1.0, "done")
    return {"score": score, "audio_info": info.to_dict(), "timing": timing}
