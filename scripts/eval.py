#!/usr/bin/env python3
"""Evaluate the full path audio -> score -> exports.

Writes reports/eval_<tag>.json with per-recording metrics at stages
(raw detections, final score) plus MIDI-export round-trip evidence.
Usage: python scripts/eval.py [--tag baseline] [--tol 0.05]
"""
import argparse
import io
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))

from piano_scribe.audio_metrics import evaluate, match_notes  # noqa: E402
from piano_scribe.notation import to_midi_bytes  # noqa: E402
from piano_scribe.pipeline import build_score  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
FIXTURES = ["synth_melody", "synth_chords", "synth_repeats", "synth_minute"]


def midi_roundtrip(score):
    import mido
    from piano_scribe.notation import beats_to_seconds, midi_beat_shift
    data = to_midi_bytes(score)
    mid = mido.MidiFile(file=io.BytesIO(data))
    shift = midi_beat_shift(score)
    # ticks -> beats -> seconds through the SAME tempo map the writer used
    notes = []
    for tr in mid.tracks:
        abs_tick = 0
        open_n = {}
        for msg in tr:
            abs_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                if msg.note in open_n:  # writer closes overlaps; mirror it
                    t0, v = open_n.pop(msg.note)
                    beat0 = t0 / mid.ticks_per_beat - shift
                    beat1 = abs_tick / mid.ticks_per_beat - shift
                    notes.append({"pitch": msg.note,
                                  "start_sec": beats_to_seconds(beat0, score.tempo),
                                  "end_sec": beats_to_seconds(beat1, score.tempo), "velocity": v})
                open_n[msg.note] = (abs_tick, msg.velocity)
            elif msg.type in ("note_off",) or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in open_n:
                    t0, v = open_n.pop(msg.note)
                    beat0 = t0 / mid.ticks_per_beat - shift
                    beat1 = abs_tick / mid.ticks_per_beat - shift
                    notes.append({"pitch": msg.note,
                                  "start_sec": beats_to_seconds(beat0, score.tempo),
                                  "end_sec": beats_to_seconds(beat1, score.tempo), "velocity": v})
    ref = [{"pitch": n.pitch, "start_sec": n.start_sec, "end_sec": n.end_sec}
           for n in score.notes]
    # Onset recall (many-to-one): MIDI has no per-note identity, so same-pitch
    # overlaps legitimately merge. What MIDI must preserve is every onset.
    found = 0
    for r in ref:
        if any(e["pitch"] == r["pitch"] and abs(e["start_sec"] - r["start_sec"]) <= 0.03
               for e in notes):
            found += 1
    m, miss, extra = match_notes(ref, notes, 0.03)
    return {"matches": len(m), "ref": len(ref), "est": len(notes),
            "match_rate": round(len(m) / len(ref), 4) if ref else 1.0,
            "onset_recall": round(found / len(ref), 4) if ref else 1.0}


def run_one(name, mode, density, tol, override=None):
    fx = os.path.join(ROOT, "fixtures", name)
    with open(fx + ".json") as f:
        gt = json.load(f)["notes"]
    t0 = time.time()
    out = build_score(fx + ".wav", mode, density, name,
                      transcriber_override=override)
    wall = round(time.time() - t0, 2)
    score = out["score"]
    dur = out["audio_info"]["duration_sec"]
    est_final = [{"pitch": n.pitch, "start_sec": n.start_sec, "end_sec": n.end_sec,
                  "velocity": n.velocity, "role": n.role} for n in score.notes]
    raw = [{"pitch": d["pitch"], "start_sec": d["start_sec"], "end_sec": d["end_sec"],
            "velocity": d.get("velocity", 80)} for d in score.raw_detections]
    res = {"fixture": name, "mode": mode, "density": density,
           "wall_sec": wall, "audio_sec": dur,
           "raw": evaluate(gt, raw, tol, dur),
           "final": evaluate(gt, est_final, tol, dur),
           "timing": out["timing"],
           "processing": {k: v for k, v in score.processing.items() if k != "audio"}}
    try:
        res["midi_roundtrip"] = midi_roundtrip(score)
    except Exception as e:  # noqa: BLE001
        res["midi_roundtrip"] = {"error": f"{type(e).__name__}: {e}"}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--tol", type=float, default=0.05)
    ap.add_argument("--transcriber", default="",
                    help="registry override for model comparison, e.g. "
                    "'piano:piano-neural' (requires weights; see docs/NEURAL_SPIKE.md)")
    args = ap.parse_args()
    override = None
    if args.transcriber:
        from piano_scribe.neural_models import register_optional_models
        from piano_scribe.audio_models import REGISTRY
        added = register_optional_models(REGISTRY)
        override = dict(kv.split(":", 1) for kv in args.transcriber.split(","))
        missing = [v for v in override.values() if v not in REGISTRY]
        if missing:
            raise SystemExit(f"transcriber(s) unavailable (added now: {added}): "
                             f"{missing}. Fetch weights first; see docs/NEURAL_SPIKE.md")
    plan = ([(f, "piano", "standard") for f in FIXTURES]
            + [("synth_chords", "arrangement", d) for d in ("simple", "standard", "detailed")]
            + [("synth_minute", "arrangement", "standard"),
               ("synth_dense", "arrangement", "standard")])
    results, failures = [], []
    for name, mode, density in plan:
        print(f"eval {name} mode={mode} density={density} ...", flush=True)
        try:
            r = run_one(name, mode, density, args.tol, override)
            results.append(r)
            print(f"  F1 raw={r['raw']['f1']} final={r['final']['f1']} "
                  f"melody={r['final']['melody_f1']} wall={r['wall_sec']}s", flush=True)
        except Exception:  # noqa: BLE001
            failures.append({"fixture": name, "mode": mode, "trace": traceback.format_exc()})
            print(f"  FAILED", flush=True)
    report = {"tag": args.tag, "tol_sec": args.tol, "results": results,
              "failures": failures,
              "note": "DSP baselines on synth fixtures. Neither mode is production-ready."}
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    path = os.path.join(ROOT, "reports", f"eval_{args.tag}.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=1)
    print(f"wrote {path} ({len(results)} ok, {len(failures)} failed)")


if __name__ == "__main__":
    main()
