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
1.00. Arrangement melody F1: chords 0.86, 1-min 1.00, dense mix 0.83;
94% of literal "extras" are harmonic voicings; 0.07 non-harmonic
extras/s on the minute excerpt. Dense-mix repeats (0.36) and fast
octave-doubled lines remain the weak spots.
Full per-recording numbers: `reports/eval_baseline.json`.

## Limitations (be honest about all of these)

1. **Synth-fixture baselines only.** No real piano or mixed-song corpus has
   been evaluated. Expect lower accuracy on reverberant, dense, detuned, or
   mastered recordings — piano-mode raw detections still over-fire on dense
   commercial mixes (arrangement mode constrains output to melody + bass +
   pads instead). Neither mode is production-ready.
2. **DSP detectors, not neural AMT.** Typical errors: soft octave doublings
   removed as ghosts, low minor-2nd clusters suppressed, fast passagework
   merged, pedal/sustain approximated by release hysteresis. On commercial
   multi-voice mixes the monophonic lead tracker chops between competing
   voices (lead/ad-libs/harmony); timbre-blind heuristics cannot separate
   them (far-hop veto ablated: broke minute 0.995 -> 0.960, reverted).
3. **Beats are estimated**, then measures laid in 4/4. Odd meters, rubato,
   and sharp tempo changes will mis-bar (audible timing stays correct —
   notation only).
4. **Notation has known gaps**: auto-detected key signature (major only;
   relative minors share it, correctly); no tuplet brackets (triplets show
   plain values), no slurs/dynamics, proportional intra-measure spacing.
   Rhythm values, beamed groups, rests, ties, chord stems, ledger lines,
   key/time signatures are fully engraved; MusicXML carries
   pitch/rhythm/voice/type/key data for real engravers.
5. **PDF mirrors the screen engraving** (same layout engine), not
   publication engraving.
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
