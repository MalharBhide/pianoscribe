"""Background worker: claims queued jobs and runs the pipeline.

Run:  python -m piano_scribe.worker [--once] [--poll 2]
Inference lives here, never in HTTP handlers.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import uuid

from .notation import render_svg, to_midi_bytes, to_musicxml, to_pdf_bytes
from .pipeline import build_score
from .store import artifact_dir, claim_next_job, get_job, save_project, update_job


def run_job(job: dict) -> str:
    jid = job["id"]

    def progress(frac: float, stage: str):
        update_job(jid, progress=max(0.0, min(1.0, frac)), stage=stage, status="working")

    try:
        update_job(jid, status="working", stage="starting", progress=0.01)
        out = build_score(job["audio_path"], job["mode"], job.get("density", "standard"),
                          job.get("title", "Untitled"), progress)
        score = out["score"]
        pid = f"prj_{uuid.uuid4().hex[:12]}"
        save_project(pid, job.get("title", "Untitled"), job["mode"], job["audio_path"],
                     score.to_dict())
        ad = artifact_dir(pid)
        with open(os.path.join(ad, "score.json"), "w") as f:
            json.dump(score.to_dict(), f)
        with open(os.path.join(ad, "score.svg"), "w") as f:
            f.write(render_svg(score))
        with open(os.path.join(ad, "score.mid"), "wb") as f:
            f.write(to_midi_bytes(score))
        with open(os.path.join(ad, "score.musicxml"), "w") as f:
            f.write(to_musicxml(score))
        with open(os.path.join(ad, "score.pdf"), "wb") as f:
            f.write(to_pdf_bytes(score))
        with open(os.path.join(ad, "timing.json"), "w") as f:
            json.dump(out["timing"], f)
        update_job(jid, status="done", stage="done", progress=1.0, project_id=pid, error="")
        return pid
    except Exception as e:  # noqa: BLE001 - worker must record, not crash
        update_job(jid, status="failed", stage="failed", error=f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args()
    print("piano-scribe worker: polling for jobs (Ctrl-C to stop)", flush=True)
    while True:
        job = claim_next_job()
        if job:
            print(f"claimed {job['id']} mode={job['mode']}", flush=True)
            pid = run_job(job)
            print(f"finished {job['id']} project={pid or '(failed)'}", flush=True)
        if args.once:
            if not job:
                print("no queued jobs", flush=True)
            return
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
