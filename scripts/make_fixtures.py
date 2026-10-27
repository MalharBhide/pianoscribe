#!/usr/bin/env python3
"""Generate deterministic synth fixtures: WAV + exact ground-truth JSON.

Each fixture: harmonic piano-ish tones (sine stack + decay), 22050 Hz mono.
Ground truth lists every note with pitch/start/end/velocity/role/hand.
"""
import json
import math
import os
import struct
import wave

SR = 22050
FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def hz(midi):
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def tone(midi, dur, vel=90, sr=SR):
    n = int(dur * sr)
    t = [i / sr for i in range(n)]
    f = hz(midi)
    out = []
    for i, tt in enumerate(t):
        env = min(1.0, tt / 0.01) * math.exp(-1.6 * tt / max(0.2, dur))
        if tt > dur - 0.03:
            env *= max(0.0, (dur - tt) / 0.03)
        v = (math.sin(2 * math.pi * f * tt)
             + 0.4 * math.sin(2 * math.pi * 2 * f * tt)
             + 0.2 * math.sin(2 * math.pi * 3 * f * tt)
             + 0.1 * math.sin(2 * math.pi * 4.003 * f * tt))
        out.append(v * env * (vel / 127.0) * 0.28)
    return out


def render(notes, path):
    end = max(n["end"] for n in notes) + 0.3
    buf = [0.0] * int(end * SR)
    for nd in notes:
        sig = tone(nd["pitch"], nd["end"] - nd["start"], nd.get("velocity", 90))
        off = int(nd["start"] * SR)
        for i, v in enumerate(sig):
            if off + i < len(buf):
                buf[off + i] += v
    peak = max(1e-6, max(abs(v) for v in buf))
    buf = [v / peak * 0.89 for v in buf]
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(struct.pack("<%dh" % len(buf), *[int(v * 32767) for v in buf]))


def save(name, notes, roles, render_notes=None):
    os.makedirs(FIX, exist_ok=True)
    notes = [dict(n, role=roles.get(i, "unknown"), hand="R" if n["pitch"] >= 60 else "L")
             for i, n in enumerate(notes)]
    # render_notes allows audio-only distractors (bits of the mix the GT
    # arrangement target deliberately excludes).
    render(render_notes if render_notes is not None else notes,
           os.path.join(FIX, name + ".wav"))
    gt = [{"pitch": n["pitch"], "start_sec": n["start"], "end_sec": n["end"],
           "velocity": n.get("velocity", 90), "role": n["role"], "hand": n["hand"]}
          for n in notes]
    with open(os.path.join(FIX, name + ".json"), "w") as f:
        json.dump({"notes": gt, "sr": SR}, f, indent=1)
    print(f"wrote {name}: {len(notes)} notes")


def main():
    # 1. melody: C major scale + repeated notes
    mel, t = [], 0.2
    for p in [60, 62, 64, 65, 67, 69, 71, 72, 72, 71, 72]:
        mel.append({"pitch": p, "start": round(t, 3), "end": round(t + 0.32, 3), "velocity": 95})
        t += 0.42
    save("synth_melody", mel, {i: "melody" for i in range(len(mel))})
    # 2. chords C-F-G-C with top melody
    prog, t = [], 0.2
    roles = {}
    for ci, ch in enumerate([[48, 52, 55, 60], [41, 45, 48, 53], [43, 47, 50, 55], [48, 52, 55, 60]]):
        for k, p in enumerate(ch):
            roles[len(prog)] = "melody" if k == 3 else ("bass" if k == 0 else "chord")
            prog.append({"pitch": p, "start": round(t, 3), "end": round(t + 0.9, 3), "velocity": 85})
        t += 1.1
    save("synth_chords", prog, roles)
    # 3. repeats + sustains: same-key repeats over held bass
    rep = [{"pitch": 36, "start": 0.2, "end": 3.4, "velocity": 80}]
    roles = {0: "bass"}
    t = 0.2
    for _ in range(6):
        roles[len(rep)] = "melody"
        rep.append({"pitch": 76, "start": round(t, 3), "end": round(t + 0.14, 3), "velocity": 100})
        t += 0.28
    rep.append({"pitch": 79, "start": round(t, 3), "end": round(t + 1.2, 3), "velocity": 90})
    roles[len(rep) - 1] = "melody"
    save("synth_repeats", rep, roles)
    # 4. one-minute progression excerpt (I–vi–IV–V loop + melody)
    long, t = [], 0.2
    roles = {}
    chords = [[48, 52, 55], [45, 48, 52], [41, 45, 48], [43, 47, 50]]
    mel_line = [72, 71, 69, 67, 69, 71, 72, 76, 74, 71, 69, 67]
    mi = 0
    while t < 55.0:
        ch = chords[int(t / 2.2) % 4]
        for k, p in enumerate(ch):
            roles[len(long)] = "bass" if k == 0 else "chord"
            long.append({"pitch": p, "start": round(t, 3), "end": round(t + 1.0, 3), "velocity": 80})
        for b in range(4):
            p = mel_line[mi % len(mel_line)]
            mi += 1
            roles[len(long)] = "melody"
            long.append({"pitch": p, "start": round(t + b * 0.5, 3),
                         "end": round(t + b * 0.5 + 0.4, 3), "velocity": 92})
        t += 2.2
    save("synth_minute", long, roles)
    # 5. dense mix: whole-note triad pads + walking 8th bass + 8th melody
    # + (audio only) quiet 16th arpeggio sparkle above the melody.
    # GT is the arrangement TARGET (melody/bass/chord); the arrangement must
    # recover it while ignoring the sparkle. Guards the salad regression.
    dense, texture = [], []
    droles = {}
    _cfg = [
        ([48, 52, 55], [72, 74, 76, 79, 76, 74, 72, 74, 72, 72, 74, 76]),
        ([45, 48, 52], [76, 79, 81, 79, 76, 74, 76, 74, 72, 74, 76, 76]),
        ([41, 45, 48], [77, 81, 77, 76, 74, 76, 77, 76, 74, 72, 74, 76]),
        ([43, 47, 50], [74, 79, 81, 83, 81, 79, 76, 74, 76, 74, 72, 74]),
    ]
    t = 0.2
    for _loop in range(2):
        for ch, mel in _cfg:
            r = ch[0]
            for k, p in enumerate(ch):
                droles[len(dense)] = "chord"  # the walking line is the bass
                # Comping stab (not a 3s sustain): walking over a sustained
                # same-pitch pad is near-invisible even to a careful listener.
                dense.append({"pitch": p, "start": round(t, 3), "end": round(t + 1.0, 3),
                              "velocity": 80})
            for b in range(12):
                droles[len(dense)] = "melody"
                dense.append({"pitch": mel[b], "start": round(t + b * 0.25, 3),
                              "end": round(t + b * 0.25 + 0.2, 3), "velocity": 100})
                wb = [r, r + 7, ch[1], r + 7][b % 4]  # root/5th/3rd walk, in bass band
                droles[len(dense)] = "bass"
                dense.append({"pitch": wb, "start": round(t + b * 0.25, 3),
                              "end": round(t + b * 0.25 + 0.22, 3), "velocity": 90})
            for s in range(24):
                ap = ch[s % 3] + 24 + (12 if s % 6 >= 3 else 0)
                texture.append({"pitch": ap, "start": round(t + s * 0.125, 3),
                                "end": round(t + s * 0.125 + 0.1, 3), "velocity": 55})
            t += 3.0
    save("synth_dense", dense, droles, render_notes=dense + texture)


if __name__ == "__main__":
    main()
