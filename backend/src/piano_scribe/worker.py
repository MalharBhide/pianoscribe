"""Background worker: claims queued jobs and runs the pipeline.

Run:  python -m piano_scribe.worker [--once] [--poll 2]
Inference lives here, never in HTTP handlers.

Each job executes in a supervised child process: on timeout or crash the
parent marks the job for retry (up to the configured attempts) or failed.
On startup, jobs left 'working' by a previous crash are recovered.
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import signal
import sys
import time
import traceback
import uuid

from .config import get_settings
from .pipeline import build_score, write_artifacts
from .score import Score
from .store import (claim_next_job, get_job, get_project, record_heartbeat,
                    recover_stale_jobs, save_project, update_job)

log = logging.getLogger("piano_scribe.worker")

_shutdown = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown
    log.info("received signal %d, finishing current job then stopping", signum)
    _shutdown = True


def run_job(job: dict) -> str:
    """Execute one job fully (called in the child process). Returns project id
    or '' when the job itself failed (already recorded on the job row)."""
    jid = job["id"]

    def progress(frac: float, stage: str):
        update_job(jid, progress=max(0.0, min(1.0, frac)), stage=stage, status="working")

    try:
        update_job(jid, status="working", stage="starting", progress=0.01)
        out = build_score(job["audio_path"], job["mode"], job.get("density", "standard"),
                          job.get("title", "Untitled"), progress)
        score = out["score"]
        try:
            params = json.loads(job.get("params") or "{}")
        except ValueError:
            params = {}
        retarget = params.get("retarget")
        if retarget and get_project(retarget):
            # Re-run onto an existing project: one Undo restores the old score.
            old = Score.from_dict(get_project(retarget)["score"])  # type: ignore[index]
            label = f"re-run ({job['mode']}, {job.get('density', 'standard')})"
            score.history = old.history + [{"label": label, "before": old._snapshot(),
                                            "after": score._snapshot()}]
            score.history = score.history[-100:]
            score.redo_stack = []
            score.version = old.version + 1
            score.title = old.title
            pid = retarget
        else:
            pid = f"prj_{uuid.uuid4().hex[:12]}"
        save_project(pid, score.title, job["mode"], job["audio_path"], score.to_dict())
        write_artifacts(pid, score, out["timing"])
        update_job(jid, status="done", stage="done", progress=1.0, project_id=pid, error="")
        return pid
    except Exception as e:  # noqa: BLE001 - worker must record, not crash
        update_job(jid, status="failed", stage="failed", error=f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return ""


def _child_main(job: dict, out_q: "mp.Queue") -> None:
    try:
        out_q.put(("ok", run_job(job)))
    except Exception as e:  # noqa: BLE001 - last-resort reporting
        traceback.print_exc()
        out_q.put(("crashed", f"{type(e).__name__}: {e}"))


def run_job_supervised(job: dict, timeout_sec: float, max_attempts: int) -> str:
    """Run a job with timeout + retry bookkeeping. Returns project id or ''."""
    jid = job["id"]
    ctx = mp.get_context("spawn")
    out_q: mp.Queue = ctx.Queue()
    proc = ctx.Process(target=_child_main, args=(job, out_q), daemon=True)
    t0 = time.time()
    proc.start()
    proc.join(timeout_sec)
    if proc.is_alive():
        log.warning("job %s exceeded %.0fs, terminating", jid, timeout_sec)
        proc.terminate()
        proc.join(10)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        return _after_abort(jid, job.get("attempts", 1), max_attempts,
                            f"timed out after {timeout_sec:.0f}s", "timeout")
    # The job row is the source of truth: a successful child always marks the
    # job done before exiting, while the result queue can lag behind join()
    # (feeder flush), so never let an empty queue override a done status.
    wall = time.time() - t0
    row = get_job(jid) or {}
    if row.get("status") == "done":
        log.info("job %s done in %.1fs project=%s", jid, wall, row.get("project_id"))
        try:
            out_q.get(timeout=10)
        except Exception:  # noqa: BLE001 - stale queue entries are harmless
            pass
        return row.get("project_id") or ""
    result = None
    try:
        result = out_q.get(timeout=10)
    except Exception:  # noqa: BLE001 - empty queue means the child died silently
        pass
    if result is None or result[0] == "crashed":
        reason = result[1] if result else f"worker process exited (code {proc.exitcode})"
        log.warning("job %s crashed after %.1fs: %s", jid, wall, reason)
        return _after_abort(jid, job.get("attempts", 1), max_attempts, reason, "crashed")
    # Child recorded its own failure (bad audio, limits, bugs): do not retry
    # blindly — retry only when attempts remain, since some failures are transient.
    row = get_job(jid) or {}
    attempts = row.get("attempts", job.get("attempts", 1))
    err = row.get("error", "failed")
    if attempts < max_attempts and _looks_transient(err):
        return _requeue(jid, f"retrying after transient failure ({err})")
    log.warning("job %s failed after %.1fs: %s", jid, wall, err)
    return ""


def _looks_transient(err: str) -> bool:
    text = (err or "").lower()
    return any(k in text for k in ("database is locked", "timeout", "temporarily",
                                   "resource temporarily", "too many open files"))


def _after_abort(jid: str, attempts: int, max_attempts: int, reason: str, kind: str) -> str:
    if attempts < max_attempts:
        return _requeue(jid, f"{kind}: {reason} (attempt {attempts}/{max_attempts})")
    update_job(jid, status="failed", stage="failed",
               error=f"{reason}; giving up after {attempts} attempts")
    return ""


def _requeue(jid: str, note: str) -> str:
    log.info("job %s requeued: %s", jid, note)
    update_job(jid, status="queued", stage=note, progress=0.0)
    # Small backoff so a poison job doesn't hot-loop the worker.
    time.sleep(5)
    return ""


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--poll", type=float, default=None)
    args = ap.parse_args()
    poll = args.poll if args.poll is not None else settings.worker_poll_sec
    worker_id = f"worker_{uuid.uuid4().hex[:8]}"
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    recovered = recover_stale_jobs(older_than_sec=settings.worker_stale_sec,
                                   max_attempts=settings.job_max_attempts)
    if recovered["requeued"] or recovered["failed"]:
        log.info("startup recovery: %s", recovered)
    log.info("%s polling (timeout=%.0fs, attempts=%d, Ctrl-C to stop)",
             worker_id, settings.job_timeout_sec, settings.job_max_attempts)
    while not _shutdown:
        record_heartbeat(worker_id)
        job = claim_next_job()
        if job:
            log.info("claimed %s mode=%s attempt=%d", job["id"], job["mode"],
                     job.get("attempts", 1))
            pid = run_job_supervised(job, settings.job_timeout_sec,
                                     settings.job_max_attempts)
            log.info("settled %s project=%s", job["id"], pid or "(not done)")
            record_heartbeat(worker_id)
        if args.once:
            if not job:
                log.info("no queued jobs")
            return
        # Sleep in small slices so shutdown is prompt.
        for _ in range(int(poll * 2)):
            if _shutdown:
                break
            time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
