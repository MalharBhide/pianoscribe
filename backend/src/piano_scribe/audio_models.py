"""Model interface + DSP baselines.

No LLM is used as a pitch detector. Stronger models (piano Onsets-and-Frames,
separators, beat trackers) implement `Transcriber` and drop in without touching
API, storage, notation, or eval. Every result records model name/version,
config, and processing version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
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
    # Clips shorter than one window: shrink the window to the input (scipy
    # shrinks nperseg but keeps a fixed noverlap, which raises). Exact match
    # (no floor) keeps the returned freq bins aligned with the spectrum.
    nperseg = min(NPERSEG, len(mono))
    noverlap = max(0, min(nperseg - 1, nperseg - HOP))
    _, t, z = stft(mono, fs=sr, window="hann", nperseg=nperseg,
                   noverlap=noverlap, padded=True, boundary="zeros")
    return np.abs(z).astype(np.float32), np.asarray(t), np.fft.rfftfreq(nperseg, 1.0 / sr)


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
        return mids, z, z, z

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
    return mids, sal, ratio, unm


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
    # Octave-safe period: dense subdivisions (8th/16th grids) often beat the
    # true beat in raw autocorrelation. Among strong local maxima, prefer
    # the period nearest 120bpm (log distance); the max stays the fallback.
    lag = int(np.argmax(ac)) if ac.max() > 0 else int(0.5 / hop_sec)
    if ac.max() > 0:
        cands = [i for i in range(1, len(ac) - 1)
                 if ac[i] >= 0.3 * ac.max() and ac[i] >= ac[i - 1] and ac[i] >= ac[i + 1]]
        if cands:
            lag = min(cands, key=lambda i: (abs(math.log((i * hop_sec) / 0.5)), -ac[i]))
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
        mids, sal, ratio, _unm = pitch_salience(mag, freqs, harmonics=int(cfg["harmonics"]))
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


def _same_harmony(a: str, b: str) -> bool:
    """Same chord for pad/symbol merging: identical, or same root where one
    tone set contains the other (a 7th coloring the tail of a triad region
    must sustain the pad, not re-strike it). dim/aug never merge (function
    differs even when tone sets nest)."""
    if a == b:
        return True
    try:
        ra, ca = a.split(":")
        rb, cb = b.split(":")
    except ValueError:
        return False
    if ra != rb or ca in ("dim", "aug") or cb in ("dim", "aug"):
        return False
    ta, tb = set(CHORD_TEMPLATES[ca]), set(CHORD_TEMPLATES[cb])
    return ta <= tb or tb <= ta


def estimate_chords(sal: np.ndarray, mids: np.ndarray, times: np.ndarray,
                    anchors: List[float],
                    melody: Optional[List[Dict[str, Any]]] = None,
                    bass: Optional[List[Dict[str, Any]]] = None,
                    fill_cap: int = 8, confirm_next: bool = True) -> List[Dict[str, Any]]:
    """Chroma-template chord labels per anchor span (shared by both modes).

    When the melody track is supplied, its exact pitch is excluded per
    frame first: otherwise a lone melody note outshines the harmony and
    every melody-only beat gets a garbage label derived from one pitch.
    When the bass track is supplied, a template rooted at the active bass
    pitch class wins near-ties (root position is the norm; true inversions
    still win when their evidence is clearly stronger).
    Weak anchors forward-fill the previous label (harmony sustains until
    contradicted), capped so long empty stretches fall back to unknown.
    """
    def tscore(vec: np.ndarray, root: int, cname: str) -> float:
        tmpl = np.zeros(12)
        for iv in CHORD_TEMPLATES[cname]:
            tmpl[(root + iv) % 12] = 1.0
        return float(np.dot(vec, tmpl) / (np.linalg.norm(tmpl) + 1e-9)
                     - 0.35 * np.dot(vec, 1 - tmpl) / 12.0)

    use = sal
    if melody:
        # Exclude the tracked melody's EXACT pitch per frame (not its whole
        # pitch class): the melody often doubles a chord tone, and killing
        # the class would erase the harmony's own evidence with it.
        use = sal.copy()
        col_of = {int(m): j for j, m in enumerate(mids)}
        for m in melody:
            j = col_of.get(int(m["pitch"]))
            if j is not None:
                sel = (times >= m["start_sec"]) & (times < m["end_sec"])
                use[sel, j] = 0.0
    chroma_frames = np.zeros((use.shape[0], 12), dtype=np.float32)
    for j, m in enumerate(mids):
        chroma_frames[:, int(m) % 12] += use[:, j]
    chords: List[Dict[str, Any]] = []
    vecs: List[np.ndarray] = []
    fill_run = 0
    for i, t0 in enumerate(anchors):
        t1 = anchors[i + 1] if i + 1 < len(anchors) else (times[-1] if len(times) else t0 + 1)
        sel = (times >= t0) & (times < t1)
        vec = chroma_frames[sel].mean(axis=0) if sel.any() else np.zeros(12, dtype=np.float32)
        if vec.max() > 0:
            vec = vec / vec.max()
        scored: List[Tuple[float, int, str]] = []
        for root in range(12):
            for cname in CHORD_TEMPLATES:
                scored.append((tscore(vec, root, cname), root, cname))
        scored.sort(reverse=True)
        best = (scored[0][2], scored[0][1], scored[0][0])
        if bass:
            # The harmony's bass is the LONGEST-sounding bass pitch in the
            # trailing 1s window: stab roots (sustained ~1s) outlast walking
            # figuration (0.2s steps), so passing tones can't re-anchor the
            # chord; ties break to the lowest (roots sit under the walk).
            # Single-onset anchors were ablated: one mistracked attack then
            # mislabels the whole span.
            bp = None
            w0 = t0 - 1.0
            tot: Dict[int, float] = {}
            for b in bass:
                if b["end_sec"] <= w0 or b["start_sec"] >= t1:
                    continue
                ov = min(b["end_sec"], t1) - max(b["start_sec"], w0)
                if ov > 0:
                    tot[b["pitch"]] = tot.get(b["pitch"], 0.0) + ov
            if tot:
                bp = min(tot, key=lambda p: (-tot[p], p))
            anchored = None
            if bp is not None:
                for (s, r, c) in scored:
                    if r == bp % 12 and s >= best[2] * 0.85:
                        anchored = (c, r, s)
                        break
            if anchored is not None:
                best = anchored
            elif chords and chords[-1]["label"] != "unknown":
                # No bass-rooted reading comes close: this span carries no
                # new harmonic evidence (melody-only tail), so sustain the
                # previous harmony via the fill below instead of adopting a
                # garbage label. The first span keeps its raw best so a
                # leading inversion still gets a label.
                best = ("unknown", best[1], 0.0)
        label = "unknown" if best[2] < 0.45 else f"{best[1]}:{best[0]}"
        if label == "unknown" and chords and chords[-1]["label"] != "unknown" \
                and fill_run < fill_cap:
            label = chords[-1]["label"]
            best = (label.split(":")[1], chords[-1]["root"], chords[-1]["score"] * 0.9)
            fill_run += 1
        else:
            fill_run = 0
        chords.append({"start_sec": float(t0), "end_sec": float(t1),
                       "label": label, "score": round(float(best[2]), 3),
                       "root": int(best[1])})
        vecs.append(vec)
    # Hysteresis: a new label must confirm itself on the NEXT anchor and
    # beat the incumbent by a margin before it takes over; single-anchor
    # flips (C vs Am sharing tones) are relabeled to the incumbent. A lone
    # unknown flanked by the same label is bridged so one weak beat does
    # not split a sustained pad.
    smooth = [dict(ch) for ch in chords]
    for i in range(1, len(smooth)):
        cur, prev = smooth[i], smooth[i - 1]
        if cur["label"] == prev["label"]:
            continue
        if cur["label"] == "unknown" and i + 1 < len(smooth) \
                and smooth[i + 1]["label"] == prev["label"]:
            cur["label"] = prev["label"]
            cur["root"] = prev["root"]
            continue
        nxt_same = i + 1 < len(smooth) and smooth[i + 1]["label"] == cur["label"]
        # Same-span comparison: re-score the incumbent template on the
        # CURRENT span's chroma. Comparing against the incumbent's stale
        # score from an earlier span glues genuine changes shut.
        inc_score = prev["score"]
        if ":" in prev["label"]:
            try:
                pr, pc = prev["label"].split(":")
                inc_score = tscore(vecs[i], int(pr), pc)
            except (ValueError, KeyError):
                pass
        confirmed = (nxt_same if confirm_next else True) \
            and cur["score"] > inc_score + 0.08
        if cur["label"] != "unknown" and not confirmed:
            cur["label"] = prev["label"]
            cur["root"] = prev["root"]
            cur["score"] = prev["score"]
    chords = smooth
    # Merge consecutive same-label spans into one sustained region so a held
    # harmony renders as one pad (and one chord symbol), not a re-struck
    # block chord on every beat. Unknown spans stay separate (rests).
    merged: List[Dict[str, Any]] = []
    for ch in chords:
        if (merged and ch["label"] != "unknown"
                and _same_harmony(ch["label"], merged[-1]["label"])
                and ch["start_sec"] - merged[-1]["end_sec"] < 0.05):
            merged[-1]["end_sec"] = ch["end_sec"]
            merged[-1]["score"] = round(max(merged[-1]["score"], ch["score"]), 3)
        else:
            merged.append(ch)
    return merged


class BaselineArranger(Transcriber):
    """Melody + bass + chords -> constrained piano arrangement.

    Emitted notes carry provenance='arranged' with evidence pointing at the
    underlying detection (detected pitch/time, chord label + score). Chord
    'unknown' yields rests, never filler.
    """
    name = "baseline-arranger"
    version = "0.1.0"
    default_config = {
        # Band floor ablated at 48 (2026-10): recovered the GT F3 but cost
        # two top notes to a louder inner voice (0.857 -> 0.50, gate red);
        # reverted. Tops lose acquisition when gated out; that needs
        # multi-frame acquisition voting, a bigger project than a constant.
        "melody_lo": 55, "melody_hi": 96, "bass_lo": 28, "bass_hi": 55,
        "density": "standard", "min_melody_sec": 0.12, "min_bass_sec": 0.18,
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
        mids, sal, _ratio, unm = pitch_salience(mag, freqs, harmonics=int(cfg["harmonics"]))
        flux = onset_flux(mag)
        if progress:
            progress(0.4, "melody")
        mel = self._track(sal, unm, mids, times, int(cfg["melody_lo"]), int(cfg["melody_hi"]),
                          float(cfg["min_melody_sec"]), 0.06, hop_penalty=0.6,
                          flux=flux)
        if progress:
            progress(0.6, "bass")
        bass = self._track(sal, unm, mids, times, int(cfg["bass_lo"]), int(cfg["bass_hi"]),
                           float(cfg["min_bass_sec"]), 0.06, hop_penalty=0.6,
                           flux=flux, prefer="low")
        if progress:
            progress(0.75, "chords")
        beats, bpm = estimate_beats(flux, times, sr)
        anchors = beats if beats else \
            list(np.arange(0, times[-1] + 1, 60.0 / 120.0 * 2))
        regions = self._harmony_regions(bass, anchors, float(times[-1]) if len(times) else 0.0)
        chords = self._chords(sal, mids, times, regions, melody=mel, bass=bass,
                              fill_cap=2, confirm_next=False)
        notes = self._arrange(mel, bass, chords, str(cfg.get("density", "standard")))
        return TranscriptionResult(notes=notes, beats=beats, bpm=bpm, chords=chords,
                                   meta=self._meta(cfg))

    def _meta(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        return {"model": self.name, "model_version": self.version,
                "config": cfg, "processing_version": PROCESSING_VERSION}

    def _track(self, sal: np.ndarray, unm: np.ndarray, mids: np.ndarray,
               times: np.ndarray, lo: int, hi: int, min_sec: float,
               floor_rel: float, hop_penalty: float,
               flux: Optional[np.ndarray] = None,
               prefer: str = "high") -> List[Dict[str, Any]]:
        cols = [j for j, m in enumerate(mids) if lo <= m <= hi]
        if not cols:
            return []
        sub = sal[:, cols]
        n = sub.shape[0]
        # Band-relative floor: the bass band carries far less energy than
        # the global max, so a global floor mutes decayed-but-real bass.
        floor = float(sub.max()) * floor_rel if sub.size else 0.0
        # Onset selection reads UNMASKED salience: greedy masking's accept
        # order can strip a genuine attack's shared partials (e.g. a melody
        # onset a 12th above an accepted bass note), destroying the evidence
        # voice assignment needs. Masked salience still validates reality
        # (ghosts fail its floor) and drives off-onset following.
        sub_u = unm[:, cols]
        floor_u = float(sub_u.max()) * floor_rel if sub_u.size else 0.0
        # Onset-gated continuity: real note changes coincide with spectral
        # onsets (hop eagerly there); mid-note leadership changes are usually
        # chatter from competing instruments (stay sticky).
        if flux is not None and len(flux) == n:
            # onset_flux is z-scored; +1 std marks genuine attack frames.
            # (Lenient values were ablated: in-band beating shimmer then
            # passes the relative-rise gate and melody F1 collapses.)
            onset = flux > 1.0
        else:
            onset = np.zeros(n, dtype=bool)
        idx = np.argmax(sub, axis=1)
        val = sub[np.arange(n), idx]
        # Frame-to-frame salience rise on UNMASKED salience (see above).
        rise = np.zeros_like(sub_u)
        rise[2:] = sub_u[2:] - sub_u[:-2]
        # In-band onsetter selection (used below): broadband flux fires for
        # ANY voice, so the newcomer must earn its place in-band.
        _pc = [int(mids[cols[c]]) for c in range(sub.shape[1])]

        def _local_max(c: int, f: int) -> bool:
            # True onsetters are the energy source (a local peak); leakage
            # into neighboring semitones is always weaker than its source.
            return all(float(sub_u[f, c]) >= float(sub_u[f, o])
                       for o in range(sub.shape[1])
                       if o != c and abs(_pc[o] - _pc[c]) <= 2)
        cur = int(idx[0])
        seq = []
        onset_split = np.zeros(n, dtype=bool)
        # Phrase-gap horizon: continuity survives short articulation gaps
        # (same phrase) but not long silences (fresh voice). ~0.3s divides
        # the two: staccato detachments are shorter, phrase breaks longer.
        hop0 = float(times[1] - times[0]) if len(times) > 1 else 0.023
        phrase_frames = max(2, int(0.3 / hop0))
        active_run = 0  # trailing active (non-silence) frames
        gap = 0  # current trailing silence run
        last_gap = 10 ** 9  # most recent completed silence run
        for f in range(n):
            if onset[f]:
                # Onsetter, judged on unmasked salience: strong above the
                # floor, a sharp relative attack (sympathetic shimmer on
                # sustained tones rises only ~10%), and a local pitch
                # maximum (leakage never beats its source). Masked salience
                # must also clear its floor (unmasked ghosts fail it).
                cands = [c for c in range(sub.shape[1])
                         if sub_u[f, c] >= floor_u and rise[f, c] > 1e-9
                         and rise[f, c] >= 0.2 * sub_u[f, c]
                         and _local_max(c, f) and sub[f, c] >= floor]
                # Prominence prior: the tracked voice is a leader, not a bit
                # player. Among co-onsetting candidates, drop qualifiers far
                # below the strongest (quiet texture above/below the line).
                if cands:
                    peak = max(float(sub_u[f, c]) for c in cands)
                    cands = [c for c in cands if sub_u[f, c] >= 0.5 * peak]
                if len(cands) > 1:
                    # Octave duplicates share all partials: the weaker twin
                    # is the harmonic shadow, not a second voice. (Fifths/
                    # 12ths are NOT collapsed: melody-over-root at a 12th is
                    # the normal case, and continuity already arbitrates it.)
                    keep = [c for c in cands if not any(
                        d != c and abs(_pc[c] - _pc[d]) == 12
                        and float(sub_u[f, d]) > float(sub_u[f, c])
                        for d in cands)]
                    cands = keep or cands
                if cands:
                    # Established voices (long active run) and same-phrase
                    # returns (short gap) compete by continuity; fresh
                    # voices after a real silence acquire by the extreme.
                    if float(sub[f, cur]) >= floor and (
                            active_run >= phrase_frames or last_gap < phrase_frames):
                        # Tracking: the voice continues NEAR itself (auditory
                        # streaming by frequency proximity). Small intervals
                        # compete freely; far jumps pay a distance penalty, so
                        # a co-onsetting sparkle/chord tone can't kidnap the
                        # line from a stepwise continuation.
                        cur_pc = _pc[cur]
                        cands.sort(key=lambda c: _pc[c],
                                   reverse=(prefer == "high"))
                        newcomer = max(
                            cands, key=lambda c: float(sub_u[f, c]) / (
                                1.0 + 0.3 * max(0, abs(_pc[c] - cur_pc) - 5)))
                    else:
                        # Acquisition (silence, or a voice too young to have
                        # continuity): take the extreme plausible voice.
                        newcomer = (max if prefer == "high" else min)(
                            cands, key=lambda c: _pc[c])
                    if newcomer == cur:
                        onset_split[f] = True  # repeated note: same pitch
                    cur = newcomer
            else:
                cand = int(idx[f])
                if cand != cur and float(sub[f, cand]) > float(sub[f, cur]) * (
                        1.0 + hop_penalty):
                    leap = abs(int(mids[cols[cand]]) - int(mids[cols[cur]]))
                    # Off-onset, only narrow motion: a large leap with no
                    # onset evidence is a louder accompaniment tone stealing
                    # the track, not a real jump (auditory streaming).
                    if leap <= 5:
                        cur = cand
            s = cur if float(sub[f, cur]) >= floor else -1
            seq.append(s)
            if s == -1:
                gap += 1
                active_run = 0
            else:
                if gap > 0:
                    last_gap = gap
                    gap = 0
                active_run += 1
        # Bridge 1-2 frame silence flicker inside a held pitch so sustained
        # notes don't split (below re-articulation scale; genuine repeated
        # notes keep their longer gaps).
        f = 0
        while f < n:
            if seq[f] == -1:
                g = f
                while g < n and seq[g] == -1:
                    g += 1
                if 0 < g - f <= 2 and f > 0 and g < n and seq[f - 1] == seq[g]:
                    for h in range(f, g):
                        seq[h] = seq[f - 1]
                f = g
            else:
                f += 1
        # Segment into notes on pitch change / silence.
        notes: List[Dict[str, Any]] = []
        start = -1
        curp = -1
        peak = 0.0
        hop_sec = float(times[1] - times[0]) if len(times) > 1 else 0.023
        min_frames = max(2, int(min_sec / hop_sec))
        for f, s in enumerate(seq + [-2]):
            p = int(mids[cols[s]]) if s >= 0 else -1
            if f < n and onset_split[f] and p == curp and f - start >= min_frames:
                # Repeated attack of the same pitch: close here, restart at f.
                notes.append({"pitch": curp, "start_sec": float(times[start]),
                              "end_sec": float(times[f]),
                              "velocity": _vel(peak, float(sal.max()) + 1e-9),
                              "peak": peak})
                start, peak = f, 0.0
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
                anchors: List[float],
                melody: Optional[List[Dict[str, Any]]] = None,
                bass: Optional[List[Dict[str, Any]]] = None,
                fill_cap: int = 8, confirm_next: bool = True) -> List[Dict[str, Any]]:
        return estimate_chords(sal, mids, times, anchors, melody=melody, bass=bass,
                               fill_cap=fill_cap, confirm_next=confirm_next)

    @staticmethod
    def _harmony_regions(bass: List[Dict[str, Any]], beats: List[float],
                         end: float) -> List[float]:
        """Harmony-analysis boundaries from bass movement (bass motion marks
        harmonic rhythm). Falls back to beats when no bass was tracked.
        Sub-second regions catch stab attacks cleanly; melody-only tails
        fall back to sustain via the unknown/fill rule downstream. (1s
        regions were ablated: they mix stab and figuration chroma into mud.)
        """
        bounds: List[float] = []
        for b in bass:
            for t in (b["start_sec"], b["end_sec"]):
                if not bounds or t - bounds[-1] >= 0.4:
                    bounds.append(float(t))
        if len(bounds) < 2:
            return list(beats)
        if beats and bounds[0] > beats[0] + 0.4:
            bounds.insert(0, float(beats[0]))
        bounds.append(end)
        out = [bounds[0]]
        for t in bounds[1:]:
            while t - out[-1] > 4.0:
                mid = [x for x in beats if out[-1] + 0.4 < x < t - 0.4]
                if not mid:
                    break
                out.append(float(mid[len(mid) // 2]))
            out.append(float(t))
        return out

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
        # Each change is clipped to the NEXT change so fast harmony never
        # stacks into giant simultaneous clusters.
        for ci, ch in enumerate(chords):
            if ch["label"] == "unknown":
                continue
            hard_end = chords[ci + 1]["start_sec"] if ci + 1 < len(chords) else ch["end_sec"]
            span_end = min(ch["end_sec"], hard_end)
            if span_end - ch["start_sec"] < 0.08:
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
                while t < span_end - 0.05 and k < 8:
                    p = tones[k % len(tones)] if tones else base
                    out.append({"pitch": p, "start_sec": t,
                                "end_sec": min(span_end, t + step * 0.9),
                                "velocity": 62, "hand": "L", "role": "chord",
                                "provenance": "arranged",
                                "evidence": {"chord": ch["label"],
                                             "chord_score": ch["score"]}})
                    t += step
                    k += 1
            else:  # standard: one soft block chord per change
                for p in tones:
                    out.append({"pitch": p, "start_sec": ch["start_sec"],
                                "end_sec": min(span_end, ch["start_sec"] + max(0.4, dur * 0.8)),
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
