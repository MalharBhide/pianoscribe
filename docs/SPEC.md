# Piano Scribe — Product & Architecture Specification (Milestone A)

Version: 0.1.0 (vertical slice) · Date: 2026-10-26 · Status: draft → implemented slice

## 1. Product vision

Piano Scribe converts uploaded recordings into readable, editable piano sheet
music. One workflow first:

Upload MP3/WAV → pick mode → watch progress → open two-staff score →
listen (rendered piano, synced cursor), compare with original, correct,
export (PDF, MIDI, MusicXML) → reopen project later.

Two modes, clearly distinguished in UI, API, storage, and evaluation:

- **A. Piano transcription** — recording already contains piano. Preserve
  performed pitches, timing, chords, articulation as accurately as possible.
- **B. Song-to-piano arrangement** — vocals + multiple instruments. Extract
  melody, bass, harmony, then build a *playable arrangement*. Detected
  evidence and arrangement choices are stored and displayed separately.

Out of MVP scope: payments, social, feeds, video, guitar tabs, mobile apps.

## 2. Architecture

```
browser ──HTTP──► API (FastAPI, typed) ──SQLite jobs──► worker (separate process)
  │                    │  │                        │
  │              static frontend             audio pipeline
  │              /api/config (real limits)    ingest → transcribe → arrange
  │                    │                      → quantize → notation → exports
  └─ SVG score, WebAudio piano, <audio> original ── storage/ (audio, artifacts, sqlite)
```

Components (small, replaceable):

| Component | Location | Responsibility |
|---|---|---|
| API | `backend/src/piano_scribe/api.py` | uploads, jobs, projects, edits, exports, assistant, config. No inference. |
| Worker | `backend/src/piano_scribe/worker.py` | polls durable queue, runs pipeline, writes progress + artifacts. |
| Queue + storage | `store.py`, `storage/` | SQLite jobs/projects, files on disk (swappable for object store). |
| Audio ingest | `audio/ingest.py` | decode MP3/WAV, validate, normalize clocks, chunk windows. |
| Models | `audio/models.py` | `Transcriber` interface; `BaselinePiano`, `BaselineArranger`; versioned. |
| Notation | `notation/` | canonical `Score`, quantization, hand assignment, SVG, MusicXML, MIDI, PDF. |
| Eval | `scripts/eval.py`, `audio/metrics.py` | note/chord/melody metrics, per-recording reports, gate check. |
| Frontend | `frontend/` | no-build HTML+JS (`app.js` serves; `app.ts` is the typed source). |

Why this shape (environment-driven): Node/npm and ffmpeg are unavailable in
this environment, so the vertical slice is dependency-light and self-contained:
Python API + worker, SQLite queue, local file storage, backend-rendered SVG
score, WebAudio piano synth, no CDN/JS build step. TypeScript sources are kept
as the typed frontend contract (`app.ts`); the served `app.js` is its
hand-compiled equivalent until `tsc` is available. All model code sits behind
`Transcriber`, so stronger models (e.g. piano Onsets-and-Frames, Demucs/HTDemucs
separation) drop in without touching API, storage, notation, or eval.

## 3. Canonical musical representation

One `Score` object drives SVG, playback, editing, MIDI, MusicXML, PDF:

- `Note`: stable id, MIDI pitch, `start_sec`/`end_sec` (performance),
  `beat_onset`/`beat_dur` + `notated` duration (notation), velocity,
  `role` (melody/bass/chord/inner/unknown), `hand` (L/R), `provenance`
  (`detected` | `arranged` | `user-edited`), `evidence` (confidence, f0, notes).
- `Score`: notes, tempo map (beat positions, changes), time signature,
  `raw_detections` (untouched model output), `processing` record
  (model name/version, config, processing version), edit history.
- Performance timing and notation quantization are stored separately; readable
  notation never silently changes audible rhythm. Chords support independent
  durations, ties across barlines, multiple voices, repeated same-pitch notes.

Edits append to history (undo/redo); raw detections are never overwritten.

## 4. Audio ingestion & long recordings

- Decode to verify (not filename sniffing). WAV via stdlib `wave`; MP3 via
  `miniaudio` wheel when installed, else clear error naming the missing
  decoder. Mono/stereo → mono-mix + keep channel info; resample to 22050 Hz
  with explicit ratio accounting; record decoder delay/padding in provenance.
- Limits are env-configurable and served verbatim: `MAX_UPLOAD_MB` (default
  50), `MAX_DURATION_SEC` (default 1800 = 30 min), `MAX_WORKER_SEC`. Frontend
  renders exactly what `/api/config` returns; no "0 MB", no "unlimited".
- Long audio: overlapping windows (default 30 s window, 5 s overlap hop
  context) with absolute sample-clock offsets, held-note carryover, onset
  dedup inside overlap, genuine repeats preserved via onset evidence.
  No cumulative drift: offsets derive from sample index, never summed floats.

## 5. ML architecture (baselines; replaceable)

- No LLM as pitch detector. LLM-class models only for dev + constrained
  score-assistant actions validated by app code.
- `BaselinePianoTranscriber` (DSP, honest baseline): STFT peak-pick in piano
  range + onset-novelty gating + note formation. Records `model_version`,
  config, processing version per score. Quiet tones preserved by relative
  (not absolute) thresholds; repeats preserved by onset evidence.
- `BaselineArranger`: predominant-melody track, low-band bass track, chroma
  template chords, onset-autocorrelation beats → constrained arrangement
  (RH melody, LH bass + voicing per density Simple/Standard/Detailed).
  Density is presentation, never labeled accuracy.
- Separation is optional preprocessing behind the same interface; its effect
  must be measured before adoption. No model chosen for newness/size/vendor.

## 6. Rhythm & notation

Beat estimation (not assumed constant); conservative reversible quantization
(snap only inside small window; never merge repeats, never split holds, never
force short notes long); rests are first-class; ties across barlines;
sustained chords keep independent releases; conventional readable SVG/PDF size.

## 7. Editor, playback, assistant

- Editor: select note/measure, pitch/duration/attack edits, add/delete,
  chord-tone edits, transpose selection, undo/redo, save/restore.
- Playback: play/pause/seek, synced cursor, click-score-to-seek, speed,
  loop measures, mute L/R, original-vs-piano toggle. One musical timeline +
  explicit audio map; seeks stay correct after edits.
- Assistant: operates on stable IDs; proposes structured, validated actions
  with preview + undo. Rule-based over score data in the slice; LLM backend
  (e.g. Meta models) plugs in behind the same action schema. Never invents
  notes or declares accuracy. Credentials backend-only.

## 8. Definition of done (vertical slice)

1. Upload WAV (MP3 when decoder present) → progress → SVG grand-staff score.
2. Piano playback synced to score; click score seeks; original/piano toggle.
3. Note/measure edits with undo/redo and persistence; exports (MID/MusicXML/
   PDF) byte-consistent with saved score version.
4. Every score records model name/version/config + processing version.
5. `/api/config` limits match env and UI text exactly.
6. `scripts/eval.py` reproduces `reports/eval_baseline.json`; per-recording
   note F1/precision/recall, onset/release errors, repeats, chords, melody,
   runtime; both modes reported separately, neither called production-ready.
7. `pytest` critical paths pass: ingest, windowing, metrics, API, exports.
8. Docs: this spec, eval protocol, model/data inventory, release checklist,
   known limitations, setup/deploy instructions.

## 9. Non-goals for this slice

Training from scratch, GPU workers, hosted separation, user accounts/billing,
real-time collaboration, full engraving (beams/slurs/dynamics), mobile apps.
