# Neural piano-model spike (candidate experiment, not shipped)

## Status (read first)

- DONE here: `Transcriber` adapter (`backend/src/piano_scribe/neural_models.py`),
  hash-verified weight fetcher (`scripts/fetch_weights.py`), eval override
  (`scripts/eval.py --transcriber piano:piano-neural`), offline tests.
- NOT DONE: weights were never downloaded and no neural eval has run — this
  sandbox has no network egress to PyPI/weight hosts (verified: HTTPS times
  out). The DSP baselines remain the models of record.
- Do not describe the neural path as validated, and do not wire it into the
  default pipeline/worker until it passes the comparison below.

## Candidate

Kong et al. high-resolution piano transcription (PyTorch), via the upstream
`piano_transcription` package's own file-based inference. Thin adapter:
audio is written to temp WAV, upstream writes MIDI, the adapter converts
with mido. Beats/chords stay DSP-estimated (recorded as hybrid in meta).
Every score records model name, checkpoint SHA, upstream package version,
config, and processing version.

## Experiment plan (spec §11 format)

- **Measured failure.** DSP piano is at ceiling on synth (melody F1 1.00,
  chords 0.81) but has no evidence on real recordings; piano-mode raw
  detections over-fire on dense commercial mixes (see KNOWN_LIMITATIONS).
  A neural piano model is expected to cut real-audio extras while holding
  recall — but that is a hypothesis until measured.
- **Why a pretrained model could address it.** Learned onset/offset/frame
  heads separate overlapping partials and pedal wash better than harmonic
  summation + hysteresis; no architecture is being trained here.
- **Dataset + licensing.** Eval first on locked real piano recordings the
  project does not yet have (MAPS/SMD/MAESTRO candidates in
  MODEL_DATA_INVENTORY). Verify code, weight, AND dataset licenses
  separately; MAESTRO/MAPS/SMD terms may restrict commercial use. Never use
  user uploads without explicit permission.
- **Splits.** By work + recording identity; release set untouched.
- **Metrics + stopping rule.** Same gate (`release_gate.yaml`) on the same
  fixtures plus the real corpus: adopt only on full-path gain with no
  concealed regression (chords, melody, repeats, sustains, drift). One
  bounded comparison; do not stack filters or tune thresholds against the
  release set. Stop and keep DSP if neural does not win.
- **Compute/storage budget.** CPU-only inference in workers (GPU unjustified
  until measured); checkpoint < 1 GiB in `PIANO_SCRIBE_MODEL_DIR`
  (outside the repo); training storage cap < 25 GB respected (no training).

## Run instructions (networked machine)

```bash
pip install piano-transcription torch --extra-index-url https://download.pytorch.org/whl/cpu
python scripts/fetch_weights.py --url <upstream-release-url> --sha256 <digest> \
    --upstream-package piano-transcription==<x.y.z>
python scripts/eval.py --tag kong1 --transcriber piano:piano-neural
python scripts/check_gate.py --tag kong1
```

Compare `reports/eval_kong1.json` against `reports/eval_baseline.json`
per-recording (not just averages), then decide per the stopping rule.
Record the decision and the losing candidate's numbers; keep DSP as the
rollback model either way.
