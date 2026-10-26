# Deploying Piano Scribe to a website

Pick one path. All run the same image: API on port 8000 + worker sharing
one persistent disk. First visit: open the URL, transcribe an example,
check `/health` and `/ready`.

## Option A: Render (easiest, no server to manage)

1. Push this repo to GitHub (done: `MalharBhide/pianoscribe`).
2. In Render: New → Blueprint → select the repo (`render.yaml` defines the
   web service + worker + shared disk).
3. Keep both services on the same plan with ≥1 GB RAM; deploy.
4. Open the web service URL. The worker starts transcribing automatically.
5. Tune env vars (`PIANO_SCRIBE_MAX_UPLOAD_MB`, …) in the dashboard.

## Option B: Fly.io (cheap, fast)

1. Install flyctl and `fly auth login`.
2. `fly launch` (accepts the shipped `fly.toml`), then:
   `fly volumes create piano_data --size 10`.
3. `fly deploy`. The `[processes]` section runs API + worker on shared
   Machines with the mounted volume.
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
