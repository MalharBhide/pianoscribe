# Production operations

## Services

- `api`: FastAPI + uvicorn (2 workers in Docker). Serves UI, API, exports.
- `worker`: one replica polls SQLite, runs transcription in a supervised
  child process (timeout + retry + crash recovery).

Single-node design: SQLite (WAL) + local disk. Scale-out swap points are
`store.py` (Postgres) and `artifact_dir`/`audio_dir` (object store).

## Configuration

All knobs are environment variables (see `.env.example` + `config.py`):
limits, CORS origins, log level, rate limits, job timeout/attempts,
worker polling/staleness. `/api/config` serves limits verbatim to the UI.

## Health & monitoring

- `GET /health` → liveness (Docker HEALTHCHECK, load balancers).
- `GET /ready` → storage writable + DB readable + worker heartbeat fresh
  (503 otherwise, with per-check reasons).
- Logs: `PIANO_SCRIBE_LOG_LEVEL`; every request logs method/path/status/
  latency + `X-Request-ID` (returned on responses, included in 500s).
- Per-job `timing.json` artifacts; eval reports checked into `reports/`.

## Reliability behavior

- Uploads: size cap, magic-byte sniffing (content beats filename), friendly
  400s, per-IP rate limits (429 with retry guidance).
- Jobs: attempts counted; supervised child killed on
  `PIANO_SCRIBE_JOB_TIMEOUT_SEC`; crashes/timeouts retried to
  `PIANO_SCRIBE_JOB_MAX_ATTEMPTS`, then failed with the reason kept.
  Startup recovery requeues jobs orphaned by a previous crash.
- Worker: heartbeat file; SIGTERM/SIGINT finishes the poll slice and exits.
- API: unhandled errors become generic 500s (tracebacks stay server-side);
  security headers on all responses; CORS off unless origins configured.

## Backups & data

Back up the storage volume (`piano_scribe.db` + `audio/` + `artifacts/`).
User uploads are never used for training/eval without explicit permission.
Rollback: redeploy the previous image tag; projects re-render from stored
score JSON independent of model version.

## Security notes

- No auth in the slice: put the service behind your SSO/VPN or add auth
  before exposing it to the open internet.
- Non-root container user; no secrets in the repo (`.env` git-ignored).
- SQLite WAL files live next to the DB; back them up together.
