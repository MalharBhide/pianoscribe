"""Model interface + DSP baselines.

No LLM is used as a pitch detector. Stronger models (piano Onsets-and-Frames,
separators, beat trackers) implement `Transcriber` and drop in without touching
API, storage, notation, or eval. Every result records model name/version,
config, and processing version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from . import PROCESSING_VERSION

MIDI_LO = 21  # A0
MIDI_HI = 108  # C8
HOP = 512
NPERSEG = 4096  # 5.4 Hz bins: separates low piano semitones; onsets still via flux


def midi_to_hz(m: float) -> float:
    return 440.0 * (2.0 ** ((m - 69.0) / 12.0))


@dataclass
class TranscriptionResult:
    notes: List[Dict[str, Any]]  # pitch,start_sec,end_sec,velocity,evidence[,attack]
    beats: List[float] = field(default_factory=list)
    bpm: float = 120.0
    chords: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


class Transcriber:
    name = "base"
    version = "0.0.0"
    default_config: Dict[str, Any] = {}

    def transcribe(self, mono: np.ndarray, sr: int,
                   progress: Optional[Callable[[float, str], None]] = None,
                   config: Optional[Dict[str, Any]] = None) -> TranscriptionResult:
        raise NotImplementedError


# -- shared DSP -------------------------------------------------------------
def stft_mag(mono: np.ndarray, sr: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    from scipy.signal import stft
    _, t, z = stft(mono, fs=sr, window="hann", nperseg=NPERSEG,
                   noverlap=NPERSEG - HOP, padded=True, boundary="zeros")
    return np.abs(z).astype(np.float32), np.asarray(t), np.fft.rfftfreq(NPERSEG, 1.0 / sr)


def _harmonic_bins(mids: np.ndarray, freqs: np.ndarray,
                   harmonics: int) -> Tuple[np.ndarray, np.ndarray]:
    """Bin index per (pitch, harmonic) + weights. Returns (B, W)."""
    bin_hz = freqs[1] - freqs[0]
    B = np.zeros((len(mids), harmonics), dtype=np.int64)
    W = np.zeros((len(mids), harmonics), dtype=np.float32)
    for j, m in enumerate(mids):
        f0 = midi_to_hz(float(m))
        for h in range(1, harmonics + 1):
            idx = int(round(f0 * h / bin_hz))
            if 0 <= idx < len(freqs):
                B[j, h - 1] = idx
                W[j, h - 1] = 0.6 ** (h - 1)
    return B, W


def pitch_salience(mag: np.ndarray, freqs: np.ndarray,
                   lo: int = MIDI_LO, hi: int = MIDI_HI,
                   harmonics: int = 4) -> Tuple[np.ndarray, np.ndarray]:
    """Harmonic-summation salience with greedy spectral masking.

    Plain harmonic summation lights up sub-harmonic and octave ghosts that
    share partials with true notes. We accept pitches greedily (strongest
    first) with two guards: (1) a fundamental-balance gate — the fundamental
    bin must carry a real share of the pitch's own score, which kills
    sub-harmonic ghosts but keeps genuine octave doublings; (2) subtraction
    of upper harmonics only, so an accepted low note never erases the
    fundamental of an octave doubling. Returns (midi_ids, sal[frames, pitches]).
    """
    mids = np.arange(lo, hi + 1)
    B, W = _harmonic_bins(mids, freqs, harmonics)
    n_bins, n_frames = mag.shape
    pad = np.zeros((n_bins + 2, n_frames), dtype=np.float32)
    pad[1:-1, :] = mag
    # neighbor radius per (pitch, harmonic): exact bin below 150 Hz (low
    # semitones sit within one bin of each other), else +-1 bin.
    bin_hz = freqs[1] - freqs[0]
    R = np.ones_like(B)
    for j, m in enumerate(mids):
        f0 = midi_to_hz(float(m))
        for h in range(1, harmonics + 1):
            if f0 * h < 150.0:
                R[j, h - 1] = 0

    def harmonic_vals(resid_col: np.ndarray) -> np.ndarray:
        center = resid_col[B + 1]
        wide = np.maximum(np.maximum(resid_col[B], center), resid_col[B + 2])
        return np.where(R == 1, wide, center)

    def score_frame(resid_col: np.ndarray) -> np.ndarray:
        return (harmonic_vals(resid_col) * W).sum(axis=1)

    # global reference from unmasked scores (sampled for speed)
    step = max(1, n_frames // 200)
    gmax = 0.0
    for f in range(0, n_frames, step):
        gmax = max(gmax, float(score_frame(pad[:, f]).max(initial=0.0)))
    if gmax <= 0:
        z = np.zeros((n_frames, len(mids)), dtype=np.float32)
        return mids, z, z

    sal = np.zeros((n_frames, len(mids)), dtype=np.float32)
    unm = np.zeros((n_frames, len(mids)), dtype=np.float32)
    for f in range(n_frames):
        unm[f] = score_frame(pad[:, f])
    accept_thr = gmax * 0.06
    for f in range(n_frames):
        resid = pad[:, f].copy()
        skipped = np.zeros(len(mids), dtype=bool)
        for _ in range(12):  # max polyphony per frame
            sc = score_frame(resid)
            sc[skipped] = -1.0
            j = int(np.argmax(sc))
            if float(sc[j]) < accept_thr:
                break
            vals = harmonic_vals(resid)
            fund_share = float(vals[j, 0] * W[j, 0]) / max(1e-9, float(sc[j]))
            if fund_share < 0.12:
                skipped[j] = True  # ghost: no real fundamental energy
                continue
            sal[f, j] = float(sc[j])
            skipped[j] = True  # each pitch accepted at most once per frame
            # subtract upper harmonics only; the fundamental bin is shared
            # with octave doublings and must survive for them.
            wsum = float(W[j, 1:].sum())
            if wsum > 0:
                take = float(sc[j]) * 0.8 / wsum
                for h in range(1, harmonics):
                    if W[j, h] > 0:
                        b = int(B[j, h]) + 1
                        resid[b] = max(0.0, resid[b] - take * W[j, h])
    ratio = np.divide(sal, unm, out=np.zeros_like(sal), where=unm > 1e-9)
    return mids, sal, ratio


def onset_flux(mag: np.ndarray) -> np.ndarray:
    logm = np.log1p(mag * 5.0)
    d = np.diff(logm, axis=1)
    d[d < 0] = 0
    flux = d.sum(axis=0)
    flux = np.concatenate([[0.0], flux])
    mu, sd = float(flux.mean()), float(flux.std() + 1e-9)
    return ((flux - mu) / sd).astype(np.float32)


def pick_peaks(x: np.ndarray, thresh: float, min_gap: int) -> List[int]:
    peaks: List[int] = []
    last = -min_gap - 1
    for i in range(1, len(x) - 1):
        if x[i] > thresh and x[i] >= x[i - 1] and x[i] > x[i + 1] and i - last > min_gap:
            peaks.append(i)
            last = i
    return peaks


def estimate_beats(flux: np.ndarray, frame_times: np.ndarray,
                   sr: int) -> Tuple[List[float], float]:
    """Tempo via autocorrelation of flux; beats by grid-anchored peak tracking."""
    hop_sec = HOP / float(sr)
    if len(flux) < 32:
        return [], 120.0
    f = flux - flux.mean()
    ac = np.correlate(f, f, mode="full")[len(f) - 1:]
    ac[: max(1, int(0.25 / hop_sec))] = 0  # ignore < 240bpm lag
    ac[int(2.0 / hop_sec):] = 0  # ignore > 30bpm lag
    lag = int(np.argmax(ac)) if ac.max() > 0 else int(0.5 / hop_sec)
    lag = max(2, lag)
    period = lag * hop_sec
    bpm = 60.0 / period
    bpm = float(min(220.0, max(40.0, bpm)))
    # Anchor: first strong flux peak, then step by period snapping to local max.
    peaks = set(pick_peaks(flux, 0.8, max(1, lag // 3)))
    if not peaks:
        return [], bpm
    start = min(peaks)
    beats = [float(frame_times[start])]
    t = start + lag
    n = len(flux)
    while t < n:
        lo, hi = max(0, t - lag // 3), min(n, t + lag // 3 + 1)
        seg = flux[lo:hi]
        t = lo + int(np.argmax(seg)) if len(seg) else t
        # snap to a real peak when one is close, else keep the grid
        near = [p for p in peaks if abs(p - t) <= max(1, lag // 4)]
        tt = min(near, key=lambda p: abs(p - t)) if near else t
        beats.append(float(frame_times[min(tt, n - 1)]))
        if len(beats) > 10000:
            break
        t = tt + lag
    return beats, bpm


# -- piano baseline ----------------------------------------------------------
class BaselinePianoTranscriber(Transcriber):
    name = "baseline-piano"
    version = "0.1.0"
    default_config = {
        "on_thresh_rel": 0.18, "off_thresh_rel": 0.10, "noise_mult": 3.0,
        "onset_thresh": 0.6, "min_note_sec": 0.06, "release_gap_frames": 4,
        "harmonics": 4,
    }

    def transcribe(self, mono: np.ndarray, sr: int,
                   progress: Optional[Callable[[float, str], None]] = None,
                   config: Optional[Dict[str, Any]] = None) -> TranscriptionResult:
        cfg = dict(self.default_config)
        if config:
            cfg.update(config)
        if progress:
            progress(0.05, "spectrum")
        mag, times, freqs = stft_mag(mono, sr)
        mids, sal, ratio = pitch_salience(mag, freqs, harmonics=int(cfg["harmonics"]))
        flux = onset_flux(mag)
        if progress:
            progress(0.45, "onsets")
        onset_frames = set(pick_peaks(flux, float(cfg["onset_thresh"]), 2))
        gmax = float(sal.max()) if sal.size else 0.0
        if gmax <= 0:
            return TranscriptionResult(notes=[], meta=self._meta(cfg))
        on_thr = gmax * float(cfg["on_thresh_rel"])
        off_thr = gmax * float(cfg["off_thresh_rel"])
        noise_floor = np.median(sal, axis=0) * float(cfg["noise_mult"])
        on_state = np.maximum(on_thr, noise_floor)
        off_state = np.maximum(off_thr, noise_floor * 0.5)

        notes: List[Dict[str, Any]] = []
        n_frames = sal.shape[0]
        hop_sec = HOP / float(sr)
        min_frames = max(2, int(float(cfg["min_note_sec"]) / hop_sec))
        gap_frames = int(cfg["release_gap_frames"])
        active: Dict[int, Dict[str, Any]] = {}  # pitch idx -> note under construction
        below: Dict[int, int] = {}
        last_end: Dict[int, float] = {}  # pitch idx -> last release frame time
        last_note: Dict[int, Dict[str, Any]] = {}
        armed: Dict[int, int] = {}  # pitch idx -> frame where attack armed

        def close_note(jj: int, end_f: int) -> None:
            note = active.pop(jj)
            below.pop(jj, None)
            note["end_sec"] = float(times[min(end_f, n_frames - 1)])
            if note["end_sec"] - note["start_sec"] >= float(cfg["min_note_sec"]):
                note["velocity"] = _vel(note.get("peak", 0.0), gmax)
                rs = note.pop("ratios", [])
                note["evidence"]["mask_ratio"] = round(float(sum(rs) / len(rs)), 3) if rs else 1.0
                note["evidence"]["peak_rel"] = round(note.get("peak", 0.0) / gmax, 3)
                notes.append(note)
                last_end[jj] = note["end_sec"]
                last_note[jj] = note

        for f in range(n_frames):
            # attack pass
            for j in range(len(mids)):
                s = float(sal[f, j])
                if j in active:
                    # release threshold follows the note's own peak through
                    # decay, so sustained notes are not split mid-hold.
                    hold_thr = max(float(off_state[j]), active[j].get("peak", 0.0) * 0.22)
                    active[j].setdefault("ratios", []).append(float(ratio[f, j]))
                    if s < hold_thr:
                        below[j] = below.get(j, 0) + 1
                        if below[j] >= gap_frames:
                            close_note(j, f - gap_frames + 1)
                    else:
                        below[j] = 0
                        if s > active[j].get("peak", 0.0):
                            active[j]["peak"] = s
                    continue
                # arm attacks early near onsets so confirmed notes backtrack
                # to the acoustic attack instead of starting late.
                if s >= float(on_state[j]) * 0.5 and any(abs(f - o) <= 3 for o in onset_frames):
                    armed.setdefault(j, f)
                elif j in armed and f - armed[j] > 8:
                    armed.pop(j, None)
                if s >= float(on_state[j]):
                    # Confirm attack: onset nearby OR strong fresh rise.
                    # Already-active pitches never re-attack without onset
                    # evidence, so holds are not split; genuine repeats carry
                    # flux peaks, so they are preserved.
                    has_onset = any(abs(f - o) <= 2 for o in onset_frames)
                    rising = f >= 3 and s > float(sal[f - 3, j]) * 1.4 + on_thr * 0.2
                    if has_onset or rising or f < 3:
                        # Re-attack right after a release with no onset in
                        # between is a split hold, not a repeat: rejoin it.
                        gap_ok = (j in last_end and
                                  float(times[f]) - last_end[j] < 0.12 and
                                  not any(last_end[j] < float(times[o]) <= float(times[f])
                                          for o in onset_frames))
                        if gap_ok and last_note.get(j) is not None:
                            active[j] = last_note.pop(j)
                            notes.remove(active[j])
                            active[j]["end_sec"] = float(times[f])
                            if s > active[j].get("peak", 0.0):
                                active[j]["peak"] = s
                            continue
                        start_f = armed.pop(j, f)
                        if f - start_f > 8:
                            start_f = f
                        if start_f != f:
                            has_onset = has_onset or any(abs(start_f - o) <= 3 for o in onset_frames)
                        active[j] = {
                            "pitch": int(mids[j]), "start_sec": float(times[start_f]),
                            "end_sec": float(times[f]), "peak": s, "attack": True,
                            "ratios": [float(ratio[f, j])],
                            "evidence": {"salience": round(s / gmax, 3),
                                         "onset": has_onset},
                        }
            if progress and f % 400 == 0:
                progress(0.45 + 0.4 * f / max(1, n_frames), "notes")
        # close remaining holds at the last frame
        for j in list(active.keys()):
            close_note(j, n_frames - 1)
        for n in notes:
            n.pop("peak", None)
            n.pop("ratios", None)
        notes = _filter_ghost_notes(notes)
        notes.sort(key=lambda d: (d["start_sec"], d["pitch"]))
        if progress:
            progress(0.9, "beats")
        beats, bpm = estimate_beats(flux, times, sr)
        anchors = beats[::2] if beats else list(np.arange(0, times[-1] + 1, 2.0))
        chords = estimate_chords(sal, mids, times, [float(a) for a in anchors])
        return TranscriptionResult(notes=notes, beats=beats, bpm=bpm,
                                   chords=chords, meta=self._meta(cfg))

    def _meta(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        return {"model": self.name, "model_version": self.version,
                "config": cfg, "processing_version": PROCESSING_VERSION}


def _vel(peak: float, gmax: float) -> int:
    v = 40 + 87 * min(1.0, peak / (gmax + 1e-9))
    return int(max(1, min(127, round(v))))


def _overlap(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    return max(0.0, min(a["end_sec"], b["end_sec"]) - max(a["start_sec"], b["start_sec"]))


def _filter_ghost_notes(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop measured ghost classes; keep everything else.

    1. Semitone neighbors of a much stronger overlapping note (same attack):
       spectral leakage, not a minor 2nd. Sequential chromatic passing tones
       are protected by the overlap + shared-onset requirements; simultaneous
       minor-2nd clusters softer than 0.7x their neighbor are the documented
       casualty.
    2. Octave/12th/2-octave-above notes with a shared attack, high overlap,
       and low relative strength: residual harmonic ghosts. Genuine doublings
       at comparable strength are kept; very soft doublings are the documented
       casualty.
    """
    if not notes:
        return notes
    strength = {id(n): n.get("evidence", {}).get("peak_rel", 0.0) for n in notes}
    drop = set()
    for n in notes:
        span = max(1e-6, n["end_sec"] - n["start_sec"])
        for m in notes:
            if m is n or id(m) in drop:
                continue
            ov = _overlap(n, m)
            if ov < 0.5 * span:
                continue
            sm, sn = strength[id(m)], max(1e-6, strength[id(n)])
            if sm < sn:
                continue
            ratio = sn / sm
            iv = n["pitch"] - m["pitch"]
            same_attack = abs(n["start_sec"] - m["start_sec"]) < 0.08
            if abs(iv) == 1 and same_attack and ov >= 0.6 * span and ratio < 0.7:
                drop.add(id(n))
                break
            mr = n.get("evidence", {}).get("mask_ratio", 1.0)
            if (iv in (12, -12, 24, -24) and same_attack and ov >= 0.7 * span
                    and ratio < 0.4):
                # octave ghost: mask_ratio cannot see it (masked and unmasked
                # are both residual-level), so relative strength decides.
                drop.add(id(n))
                break
            if (iv in (19, -19, 7, -7, 5, -5)
                    and same_attack and ov >= 0.7 * span
                    and ratio < 0.35 and mr < 0.7):
                drop.add(id(n))
                break
    return [n for n in notes if id(n) not in drop]


# -- arrangement baseline -----------------------------------------------------
CHORD_TEMPLATES = {
    "maj": [0, 4, 7], "min": [0, 3, 7], "dim": [0, 3, 6],
    "aug": [0, 4, 8], "7": [0, 4, 7, 10], "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10],
}


def estimate_chords(sal: np.ndarray, mids: np.ndarray, times: np.ndarray,
                    anchors: List[float]) -> List[Dict[str, Any]]:
    """Chroma-template chord labels per anchor span (shared by both modes)."""
    chroma_frames = np.zeros((sal.shape[0], 12), dtype=np.float32)
    for j, m in enumerate(mids):
        chroma_frames[:, int(m) % 12] += sal[:, j]
    chords: List[Dict[str, Any]] = []
    for i, t0 in enumerate(anchors):
        t1 = anchors[i + 1] if i + 1 < len(anchors) else (times[-1] if len(times) else t0 + 1)
        sel = (times >= t0) & (times < t1)
        if not sel.any():
            continue
        vec = chroma_frames[sel].mean(axis=0)
        if vec.max() <= 0:
            continue
        vec = vec / vec.max()
        best: Tuple[str, int, float] = ("unknown", 0, 0.0)
        for root in range(12):
            for cname, ivals in CHORD_TEMPLATES.items():
                tmpl = np.zeros(12)
                for iv in ivals:
                    tmpl[(root + iv) % 12] = 1.0
                score = float(np.dot(vec, tmpl) / (np.linalg.norm(tmpl) + 1e-9)
                              - 0.35 * np.dot(vec, 1 - tmpl) / 12.0)
                if score > best[2]:
                    best = (cname, root, score)
        label = "unknown" if best[2] < 0.45 else f"{best[1]}:{best[0]}"
        chords.append({"start_sec": float(t0), "end_sec": float(t1),
                       "label": label, "score": round(float(best[2]), 3),
                       "root": int(best[1])})
    return chords


class BaselineArranger(Transcriber):
    """Melody + bass + chords -> constrained piano arrangement.

    Emitted notes carry provenance='arranged' with evidence pointing at the
    underlying detection (detected pitch/time, chord label + score). Chord
    'unknown' yields rests, never filler.
    """
    name = "baseline-arranger"
    version = "0.1.0"
    default_config = {
        "melody_lo": 55, "melody_hi": 96, "bass_lo": 28, "bass_hi": 55,
        "density": "standard", "min_melody_sec": 0.12, "min_bass_sec": 0.25,
        "harmonics": 4,
    }

    def transcribe(self, mono: np.ndarray, sr: int,
                   progress: Optional[Callable[[float, str], None]] = None,
                   config: Optional[Dict[str, Any]] = None) -> TranscriptionResult:
        cfg = dict(self.default_config)
        if config:
            cfg.update(config)
        if progress:
            progress(0.05, "spectrum")
        mag, times, freqs = stft_mag(mono, sr)
        mids, sal, _ratio = pitch_salience(mag, freqs, harmonics=int(cfg["harmonics"]))
        flux = onset_flux(mag)
        gmax = float(sal.max()) if sal.size else 0.0
        if progress:
            progress(0.4, "melody")
        mel = self._track(sal, mids, times, int(cfg["melody_lo"]), int(cfg["melody_hi"]),
                          float(cfg["min_melody_sec"]), gmax * 0.10, hop_penalty=0.35)
        if progress:
            progress(0.6, "bass")
        bass = self._track(sal, mids, times, int(cfg["bass_lo"]), int(cfg["bass_hi"]),
                           float(cfg["min_bass_sec"]), gmax * 0.08, hop_penalty=0.6)
        if progress:
            progress(0.75, "chords")
        beats, bpm = estimate_beats(flux, times, sr)
        chords = self._chords(sal, mids, times, beats if beats else
                              list(np.arange(0, times[-1] + 1, 60.0 / 120.0 * 2)))
        notes = self._arrange(mel, bass, chords, str(cfg.get("density", "standard")))
        return TranscriptionResult(notes=notes, beats=beats, bpm=bpm, chords=chords,
                                   meta=self._meta(cfg))

    def _meta(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        return {"model": self.name, "model_version": self.version,
                "config": cfg, "processing_version": PROCESSING_VERSION}

    def _track(self, sal: np.ndarray, mids: np.ndarray, times: np.ndarray,
               lo: int, hi: int, min_sec: float, floor: float,
               hop_penalty: float) -> List[Dict[str, Any]]:
        cols = [j for j, m in enumerate(mids) if lo <= m <= hi]
        if not cols:
            return []
        sub = sal[:, cols]
        n = sub.shape[0]
        # Greedy continuity tracking: stay unless another pitch clearly wins.
        idx = np.argmax(sub, axis=1)
        val = sub[np.arange(n), idx]
        cur = int(idx[0])
        seq = []
        for f in range(n):
            cand = int(idx[f])
            if cand != cur and float(sub[f, cand]) > float(sub[f, cur]) * (1.0 + hop_penalty):
                cur = cand
            seq.append(cur if float(sub[f, cur]) >= floor else -1)
        # Segment into notes on pitch change / silence.
        notes: List[Dict[str, Any]] = []
        start = -1
        curp = -1
        peak = 0.0
        hop_sec = float(times[1] - times[0]) if len(times) > 1 else 0.023
        min_frames = max(2, int(min_sec / hop_sec))
        for f, s in enumerate(seq + [-2]):
            p = int(mids[cols[s]]) if s >= 0 else -1
            if p != curp:
                if curp >= 0 and start >= 0 and f - start >= min_frames:
                    notes.append({"pitch": curp, "start_sec": float(times[start]),
                                  "end_sec": float(times[min(f, n - 1)]),
                                  "velocity": _vel(peak, float(sal.max()) + 1e-9),
                                  "peak": peak})
                start, curp, peak = f, p, 0.0
            if s >= 0:
                peak = max(peak, float(sub[f, s]))
        for x in notes:
            x.pop("peak", None)
        return notes

    def _chords(self, sal: np.ndarray, mids: np.ndarray, times: np.ndarray,
                anchors: List[float]) -> List[Dict[str, Any]]:
        return estimate_chords(sal, mids, times, anchors)

    def _arrange(self, mel: List[Dict[str, Any]], bass: List[Dict[str, Any]],
                 chords: List[Dict[str, Any]], density: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in mel:
            out.append({**m, "hand": "R", "role": "melody", "provenance": "arranged",
                        "evidence": {"detected_pitch": m["pitch"],
                                     "detected_start": round(m["start_sec"], 3)}})
        for b in bass:
            out.append({**b, "hand": "L", "role": "bass", "provenance": "arranged",
                        "evidence": {"detected_pitch": b["pitch"],
                                     "detected_start": round(b["start_sec"], 3)}})
        # LH harmony from chord evidence; unknown chords -> rest (no filler).
        for ch in chords:
            if ch["label"] == "unknown":
                continue
            root, cname = ch["root"], ch["label"].split(":")[1]
            ivals = CHORD_TEMPLATES[cname][:3]
            base = 48 + root  # around C3
            tones = [base + iv for iv in ivals if 36 <= base + iv <= 64]
            dur = ch["end_sec"] - ch["start_sec"]
            if density == "simple":
                pass  # bass only
            elif density == "detailed":
                # broken-chord eighths under the span, capped to the evidence span
                step = min(0.5, max(0.22, dur / 4))
                t = ch["start_sec"]
                k = 0
                while t < ch["end_sec"] - 0.05 and k < 8:
                    p = tones[k % len(tones)] if tones else base
                    out.append({"pitch": p, "start_sec": t,
                                "end_sec": min(ch["end_sec"], t + step * 0.9),
                                "velocity": 62, "hand": "L", "role": "chord",
                                "provenance": "arranged",
                                "evidence": {"chord": ch["label"],
                                             "chord_score": ch["score"]}})
                    t += step
                    k += 1
            else:  # standard: one soft block chord per change
                for p in tones:
                    out.append({"pitch": p, "start_sec": ch["start_sec"],
                                "end_sec": min(ch["end_sec"], ch["start_sec"] + max(0.4, dur * 0.8)),
                                "velocity": 58, "hand": "L", "role": "chord",
                                "provenance": "arranged",
                                "evidence": {"chord": ch["label"],
                                             "chord_score": ch["score"]}})
        out.sort(key=lambda d: (d["start_sec"], d["pitch"]))
        return out


REGISTRY: Dict[str, Transcriber] = {
    "piano": BaselinePianoTranscriber(),
    "arrangement": BaselineArranger(),
}
