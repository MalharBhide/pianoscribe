"""Audio ingestion: decode, validate, resample, window.

All timing derives from integer sample clocks. Resampling ratios, decoder
delays, padding, and chunk offsets are recorded explicitly so long recordings
cannot drift.
"""
from __future__ import annotations

import math
import os
import wave
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np

try:
    from scipy.signal import resample_poly

    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _HAVE_SCIPY = False


class AudioError(Exception):
    pass


@dataclass
class AudioInfo:
    path: str
    codec: str
    src_sr: int
    target_sr: int
    channels: int
    n_samples_src: int
    n_samples: int
    duration_sec: float
    resample_ratio: str  # e.g. '22050/44100'
    decoder: str
    decoder_delay_samples: int = 0
    rms: float = 0.0
    leading_silence_sec: float = 0.0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "codec": self.codec, "src_sr": self.src_sr,
            "target_sr": self.target_sr, "channels": self.channels,
            "n_samples_src": self.n_samples_src, "n_samples": self.n_samples,
            "duration_sec": self.duration_sec, "resample_ratio": self.resample_ratio,
            "decoder": self.decoder, "decoder_delay_samples": self.decoder_delay_samples,
            "rms": self.rms, "leading_silence_sec": self.leading_silence_sec,
            "warnings": self.warnings,
        }


def _decode_wav(path: str) -> Tuple[np.ndarray, int, int, str]:
    try:
        with wave.open(path, "rb") as w:
            nch, width, sr, nframes = (
                w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes(),
            )
            if nframes <= 0:
                raise AudioError("empty audio file (0 frames)")
            raw = w.readframes(nframes)
    except wave.Error as e:
        raise AudioError(f"corrupt or unsupported WAV file: {e}")
    if width == 1:
        pcm = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        pcm = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise AudioError(f"unsupported WAV bit depth: {width * 8}-bit")
    if nch > 1:
        pcm = pcm.reshape(-1, nch)
    else:
        pcm = pcm.reshape(-1, 1)
    return pcm, sr, nch, "stdlib-wave"


def _decode_miniaudio(path: str) -> Tuple[np.ndarray, int, int, str]:
    try:
        import miniaudio  # type: ignore
    except ImportError:
        raise AudioError(
            "MP3 decoding needs the 'miniaudio' package, which is not installed. "
            "Install it (pip install miniaudio) or upload WAV."
        )
    try:
        info = miniaudio.get_file_info(path)
    except Exception as e:
        raise AudioError(f"could not decode audio file: {e}")
    try:
        stream = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.FLOAT32)
    except Exception as e:
        raise AudioError(f"corrupt or unsupported audio file: {e}")
    pcm = np.frombuffer(stream.samples, dtype=np.float32)
    nch = stream.nchannels
    pcm = pcm.reshape(-1, nch)
    return pcm, stream.sample_rate, nch, "miniaudio"


def _resample(pcm: np.ndarray, src_sr: int, target_sr: int) -> Tuple[np.ndarray, str]:
    if src_sr == target_sr:
        return pcm, f"{target_sr}/{src_sr}"
    frac = Fraction(target_sr, src_sr)
    up, down = frac.numerator, frac.denominator
    if _HAVE_SCIPY and max(up, down) < 1000:
        out = resample_poly(pcm, up, down, axis=0).astype(np.float32)
    else:  # fallback: linear interpolation (exact endpoint mapping, no drift)
        n_out = int(round(len(pcm) * target_sr / src_sr))
        t = np.linspace(0, len(pcm) - 1, n_out).astype(np.float64)
        idx = np.floor(t).astype(np.int64)
        frac_part = (t - idx).astype(np.float32)
        idx = np.clip(idx, 0, len(pcm) - 2)
        out = (pcm[idx] * (1 - frac_part[:, None]) + pcm[idx + 1] * frac_part[:, None]).astype(np.float32)
    return out, f"{target_sr}/{src_sr}"


def load_audio(path: str, target_sr: int = 22050,
               max_duration_sec: Optional[float] = None) -> Tuple[np.ndarray, AudioInfo]:
    """Decode + validate + mono-mix + resample. Returns (mono float32, info)."""
    if not os.path.exists(path):
        raise AudioError("file not found")
    if os.path.getsize(path) == 0:
        raise AudioError("empty file (0 bytes)")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".wav", ".wave"):
        pcm, src_sr, nch, decoder = _decode_wav(path)
        codec = "wav"
    elif ext == ".mp3":
        pcm, src_sr, nch, decoder = _decode_miniaudio(path)
        codec = "mp3"
    else:
        # Try generic decode before rejecting (extension is not the verdict).
        try:
            pcm, src_sr, nch, decoder = _decode_miniaudio(path)
            codec = ext.lstrip(".") or "unknown"
        except AudioError:
            raise AudioError(f"unsupported file type '{ext or '(none)'}': upload MP3 or WAV")
    if src_sr <= 0:
        raise AudioError("invalid sample rate in file")
    n_src = len(pcm)
    if n_src == 0:
        raise AudioError("empty audio file (0 samples after decode)")

    mono_src = pcm.mean(axis=1).astype(np.float32)
    resampled, ratio = _resample(mono_src.reshape(-1, 1), src_sr, target_sr)
    mono = resampled[:, 0]
    duration = len(mono) / float(target_sr)
    if max_duration_sec and duration > max_duration_sec:
        raise AudioError(
            f"recording is {duration / 60:.1f} min, over the configured limit of "
            f"{max_duration_sec / 60:.1f} min"
        )
    rms = float(np.sqrt(np.mean(mono ** 2))) if len(mono) else 0.0
    # Leading silence: first sample above -60 dBFS-ish threshold.
    above = np.nonzero(np.abs(mono) > 0.001)[0]
    lead_sil = float(above[0] / target_sr) if len(above) else duration
    warnings: List[str] = []
    if rms < 0.01:
        warnings.append("quiet recording (low RMS); transcription may miss soft notes")
    if lead_sil > 2.0:
        warnings.append(f"{lead_sil:.1f}s of leading silence detected")
    if duration < 1.0:
        warnings.append("very short clip (<1s); results may be sparse")
    info = AudioInfo(
        path=path, codec=codec, src_sr=src_sr, target_sr=target_sr, channels=nch,
        n_samples_src=n_src, n_samples=len(mono), duration_sec=duration,
        resample_ratio=ratio, decoder=decoder, rms=rms,
        leading_silence_sec=lead_sil, warnings=warnings,
    )
    return mono, info


@dataclass
class Window:
    index: int
    start_sample: int  # absolute, in target_sr clock
    end_sample: int
    pad_left: int = 0  # context padding actually available
    pad_right: int = 0

    @property
    def start_sec(self) -> float:
        raise NotImplementedError("convert with explicit sr via window_start_sec()")


def window_start_sec(w: Window, sr: int) -> float:
    return w.start_sample / float(sr)


def iter_windows(n_samples: int, window_sec: float, overlap_sec: float,
                 sr: int) -> Iterator[Window]:
    """Overlapping context windows on the absolute sample clock.

    Hop = window - overlap. Every window carries absolute offsets so merged
    results cannot drift; offsets are integers, never summed floats.
    """
    win = int(round(window_sec * sr))
    ov = int(round(overlap_sec * sr))
    hop = max(1, win - ov)
    if n_samples <= win:
        yield Window(index=0, start_sample=0, end_sample=n_samples)
        return
    idx = 0
    start = 0
    while start < n_samples:
        end = min(n_samples, start + win)
        yield Window(index=idx, start_sample=start, end_sample=end,
                     pad_left=min(ov, start), pad_right=max(0, start + win - n_samples))
        if end >= n_samples:
            break
        start += hop
        idx += 1


def merge_window_notes(windows: List[Tuple[Window, List[Dict[str, Any]]]],
                       sr: int, overlap_sec: float,
                       onset_tol_sec: float = 0.05) -> List[Dict[str, Any]]:
    """Merge per-window detections into one absolute-timeline list.

    Rules: keep notes whose onset falls in a window's *core* (non-overlap)
    region; carry held notes across boundaries by extending releases; dedup
    attacks inside overlaps by (pitch, onset) tolerance; genuine repeats
    (separate onset evidence) are preserved because each repeat's onset falls
    in exactly one core region.
    """
    ov_samples = int(round(overlap_sec * sr))
    merged: List[Dict[str, Any]] = []
    # active holds: pitch -> note dict still sounding at boundary
    active: Dict[int, Dict[str, Any]] = {}
    for wi, (w, notes) in enumerate(windows):
        core_start = w.start_sample + (ov_samples if wi > 0 else 0)
        for n in notes:
            onset_sample = int(round(n["start_sec"] * sr)) + w.start_sample
            if onset_sample < core_start or onset_sample >= w.end_sample:
                # Onset outside this window's core: either belongs to a
                # neighbor core or is an overlap duplicate. Matched attacks
                # extend the release; unmatched attacks with onset evidence
                # are kept (a neighbor may have missed a genuine repeat);
                # non-attacks extend active holds.
                abs_start = onset_sample / float(sr)
                matched = None
                for m in merged:
                    if m["pitch"] == n["pitch"] and abs(m["start_sec"] - abs_start) <= max(onset_tol_sec, 0.08):
                        matched = m
                        break
                if matched is not None:
                    cand_end = n["end_sec"] + w.start_sample / sr
                    if cand_end > matched["end_sec"]:
                        matched["end_sec"] = cand_end
                elif n.get("attack", True) and n.get("evidence", {}).get("onset", True):
                    det = dict(n)
                    det["start_sec"] = w.start_sample / sr + n["start_sec"]
                    det["end_sec"] = w.start_sample / sr + n["end_sec"]
                    merged.append(det)
                    active[n["pitch"]] = det
                elif n["pitch"] in active:
                    cand_end = n["end_sec"] + w.start_sample / sr
                    if cand_end > active[n["pitch"]]["end_sec"]:
                        active[n["pitch"]]["end_sec"] = cand_end
                continue
            start_abs = w.start_sample / sr + n["start_sec"]
            end_abs = w.start_sample / sr + n["end_sec"]
            # Dedup against already-merged same-pitch attacks in tolerance.
            dup = False
            for m in merged:
                if m["pitch"] == n["pitch"] and abs(m["start_sec"] - start_abs) <= onset_tol_sec:
                    # Same attack seen twice: keep earliest onset, longest release.
                    m["start_sec"] = min(m["start_sec"], start_abs)
                    m["end_sec"] = max(m["end_sec"], end_abs)
                    dup = True
                    break
            if dup:
                continue
            # Extend an active hold instead of re-attacking without evidence.
            if n["pitch"] in active and not n.get("attack", True):
                if end_abs > active[n["pitch"]]["end_sec"]:
                    active[n["pitch"]]["end_sec"] = end_abs
                continue
            det = dict(n)
            det["start_sec"] = start_abs
            det["end_sec"] = end_abs
            merged.append(det)
            active[n["pitch"]] = det
        # Drop holds that ended well before this window's end.
        w_end_sec = w.end_sample / sr
        for p in list(active):
            if active[p]["end_sec"] < w_end_sec - overlap_sec:
                del active[p]
    merged.sort(key=lambda d: (d["start_sec"], d["pitch"]))
    return merged


def estimate_duration_samples(n_samples: int, sr: int) -> float:
    return n_samples / float(sr)
