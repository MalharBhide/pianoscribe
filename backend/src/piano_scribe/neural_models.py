"""Optional neural transcribers behind the Transcriber interface.

SPIKE STATUS (honest): adapter code + fetch tooling + offline tests ship
here. No weights have been downloaded and no neural eval has run in this
environment (sandbox has no network egress for PyPI/weight hosts). Nothing
neural is wired into the default path: DSP baselines remain the models of
record until a neural candidate beats them on the release gate.

Candidate: Kong et al. high-resolution piano transcription (PyTorch).
Why: strongest public piano-only baseline with onset/offset/velocity
heads; CPU inference fits the worker model. License: inventory says code
MIT — VERIFY code, weight, and dataset terms separately before any
release; nothing here grants a license.

The adapter is deliberately thin: it shells out to the UPSTREAM package's
own file-based inference (`PianoTranscription.transcribe`) and reads the
resulting MIDI back with mido, instead of reimplementing their mel
front-end from memory. Beats/chords stay DSP-estimated (hybrid meta).
"""
from __future__ import annotations

import json
import os
import tempfile
import wave
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from . import PROCESSING_VERSION
from .audio_models import Transcriber, TranscriptionResult


class ModelNotAvailable(RuntimeError):
    """Raised when optional neural deps/weights are missing. Message always
    names the exact fetch command."""


def model_dir() -> str:
    if os.environ.get("PIANO_SCRIBE_MODEL_DIR"):
        return str(os.environ["PIANO_SCRIBE_MODEL_DIR"])
    from .config import storage_root
    return os.path.join(storage_root(), "models", "kong-piano")


def read_manifest() -> Dict[str, Any]:
    path = os.path.join(model_dir(), "manifest.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def midi_events_to_notes(midi_path: str) -> List[Dict[str, Any]]:
    """MIDI file -> note dicts (pitch/start/end seconds/velocity/evidence).

    Pure mido; unit-testable without torch or weights.
    """
    import mido
    mid = mido.MidiFile(midi_path)
    ticks_per_beat = mid.ticks_per_beat
    tempo = 500000  # default until set_tempo says otherwise
    abs_tick = 0
    abs_sec = 0.0
    open_n: Dict[int, tuple] = {}
    notes: List[Dict[str, Any]] = []
    for msg in mido.merge_tracks(mid.tracks):
        abs_tick += msg.time
        abs_sec += mido.tick2second(msg.time, ticks_per_beat, tempo)
        if msg.type == "set_tempo":
            tempo = msg.tempo
        elif msg.type == "note_on" and msg.velocity > 0:
            if msg.note in open_n:  # overlap: close previous first
                t0, v0 = open_n.pop(msg.note)
                notes.append({"pitch": msg.note, "start_sec": t0,
                              "end_sec": abs_sec, "velocity": v0,
                              "evidence": {"source": "neural"}})
            open_n[msg.note] = (abs_sec, int(msg.velocity))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            if msg.note in open_n:
                t0, v0 = open_n.pop(msg.note)
                notes.append({"pitch": msg.note, "start_sec": t0,
                              "end_sec": abs_sec, "velocity": v0,
                              "evidence": {"source": "neural"}})
    for p, (t0, v0) in open_n.items():  # unterminated: close at file end
        notes.append({"pitch": p, "start_sec": t0, "end_sec": abs_sec,
                      "velocity": v0, "evidence": {"source": "neural"}})
    notes.sort(key=lambda d: (d["start_sec"], d["pitch"]))
    return notes


class KongPianoTranscriber(Transcriber):
    """Kong et al. piano model via the upstream `piano_transcription` pkg.

    Heavy deps import lazily inside transcribe(); constructing is cheap.
    Upstream API used: PianoTranscription(device, checkpoint_path=...).
    transcribe(audio_path, midi_path). Checkpoint + manifest come from
    scripts/fetch_weights.py into model_dir().
    """
    name = "kong-piano"
    version = "checkpoint-pinned"  # exact checkpoint recorded in meta
    default_config: Dict[str, Any] = {"device": "cpu"}

    def transcribe(self, mono: np.ndarray, sr: int,
                   progress: Optional[Callable[[float, str], None]] = None,
                   config: Optional[Dict[str, Any]] = None) -> TranscriptionResult:
        cfg = dict(self.default_config)
        if config:
            cfg.update(config)
        try:
            from piano_transcription import PianoTranscription
        except ImportError as e:
            raise ModelNotAvailable(
                "piano-neural needs the upstream package: "
                "pip install piano-transcription torch --extra-index-url "
                "https://download.pytorch.org/whl/cpu ; then "
                "python scripts/fetch_weights.py --url <release-url> "
                "--sha256 <digest>") from e
        ckpt = os.path.join(model_dir(), "checkpoint.pth")
        if not os.path.exists(ckpt):
            raise ModelNotAvailable(
                f"missing checkpoint {ckpt}: run python "
                "scripts/fetch_weights.py --url <release-url> --sha256 <digest>")
        manifest = read_manifest()
        if progress:
            progress(0.05, "neural")
        with tempfile.TemporaryDirectory(prefix="ps_neural_") as td:
            wav_path = os.path.join(td, "in.wav")
            mid_path = os.path.join(td, "out.mid")
            pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(wav_path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(pcm.tobytes())
            tr = PianoTranscription(device=str(cfg.get("device", "cpu")),
                                    checkpoint_path=ckpt)
            tr.transcribe(wav_path, mid_path)
            notes = midi_events_to_notes(mid_path)
        # Hybrid timing: neural notes, DSP beats/chords (documented in meta).
        from .audio_models import (estimate_beats, estimate_chords,
                                   onset_flux, pitch_salience, stft_mag)
        mag, times, freqs = stft_mag(np.asarray(mono, dtype=np.float32), sr)
        mids, sal, _ratio, _unm = pitch_salience(mag, freqs)
        flux = onset_flux(mag)
        beats, bpm = estimate_beats(flux, times, sr)
        chords = estimate_chords(sal, mids, times, beats) if beats else []
        if progress:
            progress(1.0, "done")
        meta = {"model": self.name,
                "model_version": str(manifest.get("checkpoint", self.version)),
                "checkpoint_sha256": manifest.get("sha256", "unrecorded"),
                "upstream_package": manifest.get("upstream_package", "unrecorded"),
                "config": cfg, "beats": "dsp-estimate_beats",
                "processing_version": PROCESSING_VERSION}
        return TranscriptionResult(notes=notes, beats=beats, bpm=bpm,
                                   chords=chords, meta=meta)


def register_optional_models(registry: Dict[str, Transcriber]) -> List[str]:
    """Add available neural models to a REGISTRY dict. Never raises for
    missing optional deps: returns the list of keys actually added."""
    added: List[str] = []
    try:
        import piano_transcription  # noqa: F401
    except ImportError:
        return added
    if os.path.exists(os.path.join(model_dir(), "checkpoint.pth")):
        registry["piano-neural"] = KongPianoTranscriber()
        added.append("piano-neural")
    return added
