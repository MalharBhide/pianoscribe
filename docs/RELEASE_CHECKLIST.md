# Release checklist (0.2.0)

## Carried from 0.1.0 (re-verified)

- [x] Spec, eval protocol, model/data inventory (Milestone A).
- [x] `pytest backend/tests`: 82 passed.
- [x] `eval.py --tag baseline` reproduces `reports/eval_baseline.json`.
- [x] `check_gate.py --tag baseline`: 43/43 pass (synth-fixture thresholds).
- [x] Duration ladder 5 s → 30 min: no drift, all anchors found.
- [x] Exports + playback render from the saved score version
      (`X-Score-Version` header test; MIDI onset recall 1.00).
- [x] Limits configurable + displayed verbatim (`/api/config` test).
- [x] Model name/version/config + processing version on every score.

## New in 0.2.0

- [x] Upload → progress → studio → library flow in rebuilt frontend.
- [x] Samples gallery, MIDI import, in-browser recording, FLAC input.
- [x] Chord symbols + suggested fingering (staff, roll, MusicXML).
- [x] Re-arrange / re-transcribe onto new score versions (one Undo back).
- [x] Rename / duplicate / delete projects; search + sort library.
- [x] Assistant: whole-score transpose, display toggles, chat + chips.
- [x] Production API: request IDs, structured logs, security headers,
      optional CORS, rate limits, friendly errors, /health + /ready.
- [x] Production worker: supervised child, timeouts, retries, stale
      recovery, heartbeats, graceful shutdown.
- [x] Production Docker (non-root, healthcheck), compose, render.yaml,
      fly.toml, PRODUCTION + DEPLOY + COMPETITORS docs.
- [x] E2E probe incl. supervised worker path, samples, import, rerun.
- [x] Known limitations updated; no mode called production-accurate.

## Since 0.2.0 (on main, verified this session)

- [x] Arrangement rework: melody F1 0.86/1.00/0.83 (chords/minute/dense),
      spare melody + sustained pads, robust harmony + tempo, engraving
      polish (brace, tempo mark, 6-bar systems).
- [x] Neural spike: `Transcriber` adapter seam + hash-verified weight
      fetcher + eval override + offline tests (weights unevaluated;
      DSP stays the model of record). See `docs/NEURAL_SPIKE.md`.
- [x] Ingest hardening: corrupt-WAV errors typed as `AudioError` (were raw
      EOFError/ValueError), sub-window clips score without crashing,
      silence/extreme-rate/garbage-MP3/oversize-upload tests.

## Still open (these are what "production ready" requires)

- [ ] Real-recording piano corpus evaluated (blocks production claims).
- [ ] Real mixed-song corpus + listening panel run (blocks arrangement claims).
- [ ] Neural candidate measured against DSP on real audio (spike ready to run).
- [ ] Real MP3 bytes exercised end to end (no encoder/samples offline).
- [ ] Live deploy executed (configs ship; no cloud credentials in dev env).
- [ ] Auth/SSO before open-internet exposure (documented in PRODUCTION.md).
- [ ] Rollback drill executed against a second release.
