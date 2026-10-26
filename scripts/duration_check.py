#!/usr/bin/env python3
"""Duration ladder: verify windowing, timing integrity, and runtime scaling.

Synthesizes click-anchored tones at 5s / 30s / 1min / 5min and runs the real
pipeline, checking first/last-note absolute timing (no drift) and per-minute
cost. 15/30-min entries run the identical code path (same windows, same
merge) to prove stability at the configured 30-minute limit.
Usage: python scripts/duration_check.py [--lengths 5,30,60,300,900,1800]
"""
import argparse
import os
import struct
import sys
import time
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))

import numpy as np  # noqa: E402

from piano_scribe.pipeline import build_score  # noqa: E402

SR = 22050


def synth_minutes(path, total_sec):
    n = int(total_sec * SR)
    buf = np.zeros(n, dtype=np.float32)
    # anchor tones every ~7s + opening/closing anchors for drift checks
    marks = [0.5] + [t for t in np.arange(7.0, total_sec - 1.0, 7.0)] + [total_sec - 0.8]
    for i, t in enumerate(marks):
        p = 60 + (i * 5) % 12
        f = 440.0 * 2 ** ((p - 69) / 12)
        i0, i1 = int(t * SR), min(n, int((t + 0.4) * SR))
        tt = np.arange(i1 - i0) / SR
        buf[i0:i1] += (np.sin(2 * np.pi * f * tt) * np.minimum(1, tt / 0.01)
                       * np.exp(-3 * tt) * 0.5).astype(np.float32)
    buf /= max(1e-6, np.abs(buf).max())
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(struct.pack("<%dh" % n, *(int(v * 30000) for v in buf)))
    return marks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lengths", default="5,30,60,300")
    args = ap.parse_args()
    tmp = "/tmp/piano_scribe_dur"
    os.makedirs(tmp, exist_ok=True)
    print(f"{'length':>8} {'windows':>8} {'wall':>8} {'notes':>7} "
          f"{'first_err':>9} {'last_err':>9} {'drift/min':>9}")
    for L in [float(x) for x in args.lengths.split(",") if x.strip()]:
        path = os.path.join(tmp, f"dur_{L:g}.wav")
        marks = synth_minutes(path, L)
        t0 = time.time()
        out = build_score(path, "piano", "standard", f"dur{L:g}")
        wall = time.time() - t0
        notes = sorted(out["score"].notes, key=lambda x: x.start_sec)
        wins = out["score"].processing.get("windows", 1)
        fe = min([abs(x.start_sec - marks[0]) for x in notes], default=float("nan"))
        le = min([abs(x.start_sec - marks[-1]) for x in notes], default=float("nan"))
        # Drift is meaningful only over longer spans; short lengths report n/a.
        drift = (le - fe) / (L / 60) * 1000 if (notes and L >= 60) else float("nan")
        dstr = f"{drift:>8.1f}m" if L >= 60 else f"{'n/a':>9}"
        print(f"{L:>8g} {wins:>8} {wall:>8.1f} {len(notes):>7} "
              f"{fe * 1000:>8.0f}m {le * 1000:>8.0f}m {dstr}")
    print("drift/min < 20ms required by release gate (drift_ms_per_min_max).")


if __name__ == "__main__":
    main()
