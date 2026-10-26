# Piano Scribe 0.2.0

Upload a recording → get a readable, editable piano score. Two modes:
**Piano transcription** (recording has piano) and **Song-to-piano
arrangement** (band/vocals → playable piano). Staff + piano-roll studio,
synced piano playback, note editing with undo, score assistant, chord
symbols, suggested fingering, MIDI import, example gallery, in-browser
recording, MIDI/MusicXML/PDF export, project library.

> Transcription *models* are DSP baselines with measured synth-fixture
> quality (see `reports/`); real-recording corpora are required before any
> production-accuracy claim. The *application* (API, worker, editor,
> packaging, docs) is production-structured: hardened, observable, and
> deployable — see `docs/PRODUCTION.md` and `docs/DEPLOY.md`.

## Quick start (Docker — one command)

Requires Docker Desktop (or Docker Engine + Compose v2). No Python, Node,
or ffmpeg needed on your machine.

```bash
cd piano-scribe
docker compose up --build
```

That's the whole setup: one command starts the API (the app) and the
background worker (transcriptions) together, sharing one data volume.
First run takes a few minutes to build the image; later starts are fast.

Open http://127.0.0.1:8000 in a browser and either upload audio
(MP3/WAV/FLAC), record in the browser, import a MIDI file, or transcribe
a bundled example. Pick a mode, then polish the score in the studio.

Useful commands (from the repo root):

```bash
docker compose up -d --build   # start in the background
docker compose logs -f         # watch API + worker logs
docker compose ps              # both services should show healthy
docker compose down            # stop (keeps your scores)
docker compose down -v         # stop AND delete all scores/audio
```

Your projects, audio, and jobs persist in the `piano-scribe-data` Docker
volume across restarts. Copy `.env.example` to `.env` to change limits.

To put it on a website instead of localhost, follow `docs/DEPLOY.md`
(Render, Fly.io, or any VPS with Docker).

## Developing without Docker

Only needed to hack on the code, run tests, or reproduce quality numbers.
Requires Python 3.9+ (3.10+ recommended).

```bash
cd piano-scribe
python3 -m venv .venv && source .venv/bin/activate
pip install -e "backend[test]"
python scripts/make_fixtures.py

# Terminal 1: API (serves the app at http://127.0.0.1:8000)
uvicorn piano_scribe.api:app --host 127.0.0.1 --port 8000

# Terminal 2: background worker (runs transcription jobs)
python -m piano_scribe.worker
```

Run from the repo root with the venv active so `piano_scribe` resolves
(editable install) and `storage/` lands in the project.

```bash
python -m pytest backend/tests -q          # unit + integration tests
python scripts/eval.py --tag baseline      # writes reports/eval_baseline.json
python scripts/check_gate.py --tag baseline
python scripts/duration_check.py --lengths 5,30,60,300,900,1800
```

## Configuration (actual limits, served verbatim at `/api/config`)

| Variable | Default | Meaning |
|---|---|---|
| `PIANO_SCRIBE_MAX_UPLOAD_MB` | 50 | upload size cap (MB) |
| `PIANO_SCRIBE_MAX_DURATION_SEC` | 1800 | max audio length (30 min) |
| `PIANO_SCRIBE_MAX_WORKER_SEC` | 3600 | worker budget hint |
| `PIANO_SCRIBE_TARGET_SR` | 22050 | internal sample rate |
| `PIANO_SCRIBE_STORAGE` | `./storage` | jobs DB, audio, artifacts |
| `PIANO_SCRIBE_WINDOW_SEC` / `_OVERLAP_SEC` | 30 / 5 | long-file windowing |

Copy `.env.example` to `.env` to change them. The UI renders exactly what
the server reports — never "0 MB", never "unlimited".

Rollback: every score records `model`, `model_version`, `config`,
`processing_version`. Redeploy the previous image tag (see
`docs/RELEASE_CHECKLIST.md`); old projects keep rendering from their
stored score JSON regardless of model version.

## Layout

- `backend/src/piano_scribe/` — API, worker, SQLite store, ingest, DSP
  models (`audio_models.py`, replaceable via `Transcriber`), canonical
  `Score`, notation + exports, assistant
- `frontend/` — no-build app (`index.html`, `app.js`; `app.ts` typed source)
- `fixtures/` — deterministic synth WAV + ground truth
- `scripts/` — fixtures, eval, gate check, duration ladder
- `reports/` — measured results (checked in for the baseline)
- `docs/` — spec, eval protocol, model/data inventory, release gate,
  listening rubric, checklist, limitations, production, deploy, competitors
- `storage/` — runtime DB/audio/artifacts (git-ignored)

## Docs

Start with `docs/SPEC.md`, then `docs/EVALUATION_PROTOCOL.md`,
`docs/KNOWN_LIMITATIONS.md`, `docs/RELEASE_CHECKLIST.md`.
Operators read `docs/PRODUCTION.md` + `docs/DEPLOY.md`; product context is
in `docs/COMPETITORS.md`.
