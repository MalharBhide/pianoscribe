# Deploying Piano Scribe to a website

Pick one path. Every option runs the API and the transcription worker
against ONE shared disk — the worker must see the same jobs database and
audio files the API wrote, otherwise uploads queue forever. First visit:
open the URL, transcribe an example, check `/health` and `/ready`.

> Render disks and Fly volumes cannot be shared between separate
> services/machines, so both blueprints run API + worker inside one
> container via `scripts/start_all.sh` (honors `$PORT`). Do not split
> them into two services without also moving jobs/storage to Postgres/S3.

## Option A: Render (easiest, no server to manage)

1. Push this repo to GitHub (done: `MalharBhide/pianoscribe`).
2. In Render: New → Blueprint → select the repo (`render.yaml` defines a
   single web service: API + worker on one disk).
3. Use a plan with ≥1 GB RAM; deploy.
4. Open the service URL. The worker starts transcribing automatically.
5. Tune env vars (`PIANO_SCRIBE_MAX_UPLOAD_MB`, …) in the dashboard.

## Option B: Fly.io (cheap, fast)

1. Install flyctl and `fly auth login`.
2. `fly launch` (accepts the shipped `fly.toml`), then:
   `fly volumes create piano_data --size 10`.
3. `fly deploy`. The single `app` process runs API + worker together on
   the mounted volume.
4. `fly open` to use it; `fly logs` to watch jobs.

## Option C: Any VPS with Docker (most control)

1. Copy the repo to the server, create `.env` from `.env.example`.
2. `docker compose up -d --build`.
3. Put Caddy/Nginx in front for HTTPS, proxying to `127.0.0.1:8000`.
4. Back up the `piano-scribe-data` volume on a schedule.

## After deploy checklist

- `/health` returns `{"status":"ok"}`; `/ready` reports worker alive.
- Transcribe a bundled example end-to-end; export MIDI + PDF.
- Confirm uploads over the size limit get a friendly error.
- Set up disk-usage alerts (audio + artifacts grow with use).

> Status: Docker/Render/Fly configs ship with the repo but live deploys
> were not executed from this environment (no cloud credentials here).
> The checklist above is the acceptance test for whoever clicks deploy.
