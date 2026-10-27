# Baseline quality report (tag=baseline, tol=±50ms)

DSP baselines on deterministic synth fixtures. Neither mode is production-ready.

| Fixture | Mode | Density | F1 | Prec | Rec | Extra | Miss | Repeat | Chord | Melody F1 | x/s | MIDI on-rec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| synth_melody | piano | standard | 1 | 1 | 1 | 0 | 0 | 1.0 | 1 | 1 | 0 | 1 |
| synth_chords | piano | standard | 0.8108 | 0.7143 | 0.9375 | 6 | 1 | 1.0 | 0.75 | 1 | 1.277 | 1 |
| synth_repeats | piano | standard | 0.8235 | 0.7778 | 0.875 | 2 | 1 | 1.0 | 1 | 1 | 0.5405 | 1 |
| synth_minute | piano | standard | 0.8373 | 0.7202 | 1 | 68 | 0 | 1.0 | 1 | 1 | 1.232 | 1 |
| synth_chords | arrangement | simple | 0.6087 | 1 | 0.4375 | 0 | 9 | 1.0 | 0 | 0.8571 | 0 | 1 |
| synth_chords | arrangement | standard | 0.6207 | 0.6923 | 0.5625 | 4 | 7 | 1.0 | 0.25 | 0.8571 | 0.8511 | 1 |
| synth_chords | arrangement | detailed | 0.4118 | 0.3889 | 0.4375 | 11 | 9 | 1.0 | 0 | 0.8571 | 2.34 | 1 |
| synth_minute | arrangement | standard | 0.7297 | 0.6748 | 0.7943 | 67 | 36 | 1.0 | 0.28 | 0.995 | 1.214 | 1 |
| synth_dense | arrangement | standard | 0.7991 | 0.8164 | 0.7824 | 38 | 47 | 0.36 | 0.25 | 0.8254 | 1.553 | 1 |

Onset p95 ≤ 40 ms on all runs; release/duration accuracy 1.00 on short fixtures.
Arrangement extras are 94% harmonic voicings on the minute excerpt (see harmonic_extra in eval JSON).
Reproduce: `python scripts/eval.py --tag baseline && python scripts/check_gate.py --tag baseline`.
