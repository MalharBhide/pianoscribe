"""Note/chord/melody metrics. One-to-one pitch/onset matching + timing errors."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np


def match_notes(ref: List[Dict[str, Any]], est: List[Dict[str, Any]],
                tol_sec: float = 0.05) -> Tuple[List[Tuple[int, int, float]], List[int], List[int]]:
    """Greedy one-to-one matching by onset error. Returns (matches, ref_miss, est_extra)."""
    order = sorted(range(len(est)), key=lambda i: est[i]["start_sec"])
    used = set()
    matches: List[Tuple[int, int, float]] = []
    for j in order:
        best = None
        for i, r in enumerate(ref):
            if i in used or r["pitch"] != est[j]["pitch"]:
                continue
            err = abs(r["start_sec"] - est[j]["start_sec"])
            if err <= tol_sec and (best is None or err < best[1]):
                best = (i, err)
        if best is not None:
            used.add(best[0])
            matches.append((best[0], j, est[j]["start_sec"] - ref[best[0]]["start_sec"]))
    miss = [i for i in range(len(ref)) if i not in used]
    matched_est = {m[1] for m in matches}
    extra = [j for j in range(len(est)) if j not in matched_est]
    return matches, miss, extra


def _stats(x: List[float]) -> Dict[str, float]:
    if not x:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0, "n": 0}
    a = np.array(sorted(abs(v) for v in x))
    return {"mean": round(float(a.mean()), 4), "median": round(float(np.median(a)), 4),
            "p95": round(float(a[min(len(a) - 1, int(len(a) * 0.95))]), 4), "n": len(x)}


def evaluate(ref: List[Dict[str, Any]], est: List[Dict[str, Any]],
             tol_sec: float = 0.05, duration_sec: float = 0.0,
             melody_role: str = "melody") -> Dict[str, Any]:
    matches, miss, extra = match_notes(ref, est, tol_sec)
    n_ref, n_est, n_hit = len(ref), len(est), len(matches)
    prec = n_hit / n_est if n_est else 0.0
    rec = n_hit / n_ref if n_ref else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    onset_err = [m[2] for m in matches]
    rel_err = [est[j]["end_sec"] - ref[i]["end_sec"] for i, j, _ in matches]
    dur_ok = 0
    for i, j, _ in matches:
        rd = max(0.03, ref[i]["end_sec"] - ref[i]["start_sec"])
        ed = max(0.0, est[j]["end_sec"] - est[j]["start_sec"])
        if abs(ed - rd) <= max(0.1, 0.2 * rd):
            dur_ok += 1
    # repeated notes: same-pitch ref pairs with gap < 0.5s
    reps_total, reps_kept = 0, 0
    by_pitch: Dict[int, List[float]] = {}
    for r in ref:
        by_pitch.setdefault(r["pitch"], []).append(r["start_sec"])
    est_by_pitch: Dict[int, List[float]] = {}
    for e in est:
        est_by_pitch.setdefault(e["pitch"], []).append(e["start_sec"])
    for p, ts in by_pitch.items():
        ts.sort()
        for a, b in zip(ts, ts[1:]):
            if b - a < 0.5:
                reps_total += 1
                es = [t for t in est_by_pitch.get(p, []) if a - tol_sec <= t <= b + tol_sec]
                if len(es) >= 2:
                    reps_kept += 1
    # chords: ref simultaneities >=3 notes within 50ms
    chords_total, chords_full = 0, 0
    extra_in_chord: List[int] = []
    if ref:
        groups: List[List[Dict[str, Any]]] = []
        cur: List[Dict[str, Any]] = [ref[0]]
        for r in sorted(ref, key=lambda d: d["start_sec"])[1:]:
            if r["start_sec"] - cur[0]["start_sec"] <= 0.05:
                cur.append(r)
            else:
                groups.append(cur)
                cur = [r]
        groups.append(cur)
        matched_ref = {m[0] for m in matches}
        ref_idx = {id(r): i for i, r in enumerate(ref)}
        for g in groups:
            if len(g) < 3:
                continue
            chords_total += 1
            if all(ref_idx[id(r)] in matched_ref for r in g):
                chords_full += 1
            t0 = g[0]["start_sec"]
            near_extra = [e for e in extra
                          if abs(est[e]["start_sec"] - t0) <= 0.1]
            extra_in_chord.append(len(near_extra))
    # melody subset
    ref_mel = [r for r in ref if r.get("role") == melody_role]
    est_mel = [e for e in est if e.get("role") == melody_role]
    if ref_mel:
        if est_mel:
            mm, _, _ = match_notes(ref_mel, est_mel, tol_sec)
            mel_prec = len(mm) / len(est_mel)
        else:
            # Estimator emits no role labels (e.g. piano mode): report melody
            # coverage against all estimates; precision mirrors recall so the
            # F1 reads as coverage, not as a false zero.
            mm, _, _ = match_notes(ref_mel, est, tol_sec)
            mel_prec = len(mm) / len(ref_mel) if ref_mel else 0.0
        mel_rec = len(mm) / len(ref_mel)
        mel_f1 = 2 * mel_prec * mel_rec / (mel_prec + mel_rec) if (mel_prec + mel_rec) else 0.0
    else:
        mel_prec = mel_rec = mel_f1 = 0.0
    extra_per_sec = len(extra) / duration_sec if duration_sec > 0 else 0.0
    # Harmonic extras: valid voicings, not clutter. An extra whose pitch class
    # sounds in the reference within +-1s is consonant with the local harmony.
    harmonic_extra, nonharmonic_extra = 0, 0
    for j in extra:
        e = est[j]
        pcs = {r["pitch"] % 12 for r in ref if abs(r["start_sec"] - e["start_sec"]) <= 1.0}
        if e["pitch"] % 12 in pcs:
            harmonic_extra += 1
        else:
            nonharmonic_extra += 1
    nonharmonic_xps = nonharmonic_extra / duration_sec if duration_sec > 0 else 0.0
    return {
        "tol_sec": tol_sec, "n_ref": n_ref, "n_est": n_est, "n_hit": n_hit,
        "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
        "onset_err_sec": _stats(onset_err), "release_err_sec": _stats(rel_err),
        "duration_acc": round(dur_ok / n_hit, 4) if n_hit else 0.0,
        "extra": len(extra), "missing": len(miss),
        "extra_per_sec": round(extra_per_sec, 4),
        "harmonic_extra": harmonic_extra, "nonharmonic_extra": nonharmonic_extra,
        "nonharmonic_extra_per_sec": round(nonharmonic_xps, 4),
        "repeat_total": reps_total,
        "repeat_preserved": reps_kept,
        "repeat_rate": round(reps_kept / reps_total, 4) if reps_total else 1.0,
        "chords_total": chords_total, "chords_full": chords_full,
        "chord_recovery": round(chords_full / chords_total, 4) if chords_total else 1.0,
        "extra_in_chord_mean": round(float(np.mean(extra_in_chord)), 3) if extra_in_chord else 0.0,
        "melody_precision": round(mel_prec, 4), "melody_recall": round(mel_rec, 4),
        "melody_f1": round(mel_f1, 4),
    }
