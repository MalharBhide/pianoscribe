# Baseline quality report (tag=baseline, tol=±50ms)

DSP baselines on deterministic synth fixtures. Neither mode is production-ready.

| Fixture | Mode | Density | F1 | Prec | Rec | Extra | Miss | Repeat | Chord | Melody F1 | x/s | MIDI on-rec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| synth_melody | piano | standard | 1.0 | 1.0 | 1.0 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.0 | 1.0 |
| synth_chords | piano | standard | 0.8108 | 0.7143 | 0.9375 | 6 | 1 | 1.0 | 0.75 | 1.0 | 1.2766 | 1.0 |
| synth_repeats | piano | standard | 0.8235 | 0.7778 | 0.875 | 2 | 1 | 1.0 | 1.0 | 1.0 | 0.5405 | 1.0 |
| synth_minute | piano | standard | 0.8373 | 0.7202 | 1.0 | 68 | 0 | 1.0 | 1.0 | 1.0 | 1.2319 | 1.0 |
| synth_chords | arrangement | simple | 0.6087 | 1.0 | 0.4375 | 0 | 9 | 1.0 | 0.0 | 0.8571 | 0.0 | 1.0 |
| synth_chords | arrangement | standard | 0.6316 | 0.5455 | 0.75 | 10 | 4 | 1.0 | 0.5 | 0.8571 | 2.1277 | 1.0 |
| synth_chords | arrangement | detailed | 0.3902 | 0.32 | 0.5 | 17 | 8 | 1.0 | 0.0 | 0.8571 | 3.617 | 1.0 |
| synth_minute | arrangement | standard | 0.4194 | 0.2921 | 0.7429 | 315 | 45 | 1.0 | 0.08 | 0.8858 | 5.7065 | 1.0 |

Onset p95 ≤ 40 ms on all runs; release/duration accuracy 1.00 on short fixtures.
Arrangement extras are 83% harmonic voicings on the minute excerpt (see harmonic_extra in eval JSON).
Reproduce: `python scripts/eval.py --tag baseline && python scripts/check_gate.py --tag baseline`.
