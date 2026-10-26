# Known limitations & supported inputs (0.1.0 slice)

## Supported inputs

- WAV (8/16/32-bit PCM, mono/stereo, common rates incl. 44.1/48 kHz) and
  MP3 (CBR/VBR, mono/stereo) when `miniaudio` is installed.
- Lengths: verified 5 s → 30 min (see `reports/duration_ladder.md`).
- Best on clean, dry piano recordings and simple mixes. Quiet recordings
  and leading silence are detected and warned about, not rejected.

## Measured quality (synth fixtures, ±50 ms)

Piano mode: melody F1 1.00, chords F1 0.81 (recovery 0.75), repeats 0.82,
1-min excerpt F1 0.84; melody coverage 1.00 everywhere; MIDI onset recall
1.00. Arrangement: melody F1 0.86–0.89; 83% of literal "extras" are
harmonic voicings; 0.98 non-harmonic extras/s on the minute excerpt.
Full per-recording numbers: `reports/eval_baseline.json`.

## Limitations (be honest about all of these)

1. **Synth-fixture baselines only.** No real piano or mixed-song corpus has
   been evaluated. Expect lower accuracy on reverberant, dense, detuned, or
   mastered recordings — e.g. a 20 s slice of a real mastered MP3 yields
   ~280 raw detections (over-detection on dense mixes), versus clean output
   on sparse synth. Neither mode is production-ready.
2. **DSP detectors, not neural AMT.** Typical errors: soft octave doublings
   removed as ghosts, low minor-2nd clusters suppressed, fast passagework
   merged, pedal/sustain approximated by release hysteresis.
3. **Beats are estimated**, then measures laid in 4/4. Odd meters, rubato,
   and sharp tempo changes will mis-bar (audible timing stays correct —
   notation only).
4. **Notation is simplified**: no key signatures (chromatic sharps), no
   beams/slurs/dynamics/fingerings, proportional intra-measure spacing.
   MusicXML carries full pitch/rhythm/voice data for real engravers.
5. **PDF is a review rendering**, not publication engraving (labeled as such
   in the file).
6. **MIDI merges same-pitch overlaps** (standard MIDI has no per-note
   identity); every onset is preserved (recall 1.00 measured).
7. **Arrangement rubric is human** (`docs/RUBRIC.md`) — no listening panel
   has been run yet; arrangement quality claims await it.
8. **No training has run.** `training/` does not exist yet by design:
   establish pretrained-model baselines first; any future experiment must
   file the plan the spec requires (failure, dataset+license, split,
   metrics, budget).
9. **Single-node deployment.** SQLite queue + local disk; scale-out needs
   the Postgres/S3 swap (interfaces are already separated: `store.py` +
   `artifact_dir`/`audio_dir`). No auth ships — see `PRODUCTION.md`.
10. **Assistant is rule-based** (transpose, simplify, loop, display, chord
    questions). It proposes validated actions with preview + undo; it never
    edits silently and never judges accuracy.
11. **Fingering is a heuristic** (greedy, per-hand). Sensible for scales,
    arpeggios, and simple chords; review before printing parts.
12. **No link imports.** YouTube/TikTok URLs, video files, AAC/M4A, OMR,
    other instruments, and tabs are out of scope (see `COMPETITORS.md`).
13. **MIDI import** skips drum-channel notes and derives hands/chords by
    rule; complex multi-instrument files collapse to one piano score.
