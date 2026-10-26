"""HTTP API. No model inference runs in request handlers.

Uploads are saved + queued; the worker process does audio/ML work.
Exports and SVG are rendered from the saved score version on demand.
"""
from __future__ import annotations

import copy
import logging
import os
import shutil
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from . import PROCESSING_VERSION
from .assistant import apply_action, parse_command
from .config import get_limits, get_settings
from .notation import render_svg, to_midi_bytes, to_musicxml, to_pdf_bytes
from .score import Score
from .store import (artifact_dir, audio_dir, audio_referenced_elsewhere,
                    create_job, delete_project, get_job, get_project,
                    heartbeat_status, list_jobs, list_projects, save_project,
                    storage_root)

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = REPO_ROOT / "frontend"

log = logging.getLogger("piano_scribe.api")


def setup_logging() -> None:
    level = getattr(logging, get_settings().log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


setup_logging()

app = FastAPI(title="Piano Scribe", version="0.2.0")


# -- middleware ---------------------------------------------------------------
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        t0 = time.time()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        log.info("%s %s -> %d (%.0fms) rid=%s", request.method, request.url.path,
                 response.status_code, (time.time() - t0) * 1000, rid)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response


app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
_settings = get_settings()
if _settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_settings.cors_origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    rid = getattr(request.state, "request_id", "unknown")
    log.error("unhandled error rid=%s: %s\n%s", rid, exc, traceback.format_exc())
    return JSONResponse(
        {"detail": "Something went wrong on our end. Please try again.",
         "request_id": rid},
        status_code=500,
    )


# -- rate limiting (in-memory sliding window, per client IP) -------------------
class RateLimiter:
    def __init__(self) -> None:
        self.hits: Dict[str, List[float]] = {}

    def check(self, key: str, limit_per_min: int) -> bool:
        if limit_per_min <= 0:
            return True
        now = time.time()
        window = [t for t in self.hits.get(key, []) if now - t < 60.0]
        if len(window) >= limit_per_min:
            self.hits[key] = window
            return False
        window.append(now)
        self.hits[key] = window
        return True

    def reset(self) -> None:
        self.hits.clear()


rate_limiter = RateLimiter()


def reset_rate_limits() -> None:
    rate_limiter.reset()


def _client_key(request: Request) -> str:
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


def _rate_or_429(request: Request, bucket: str, limit: int) -> None:
    if not rate_limiter.check(f"{bucket}:{_client_key(request)}", limit):
        raise HTTPException(429, "Too many requests — please wait a minute and try again.")


# -- upload content sniffing -----------------------------------------------------
def sniff_audio_kind(data: bytes) -> str:
    """Detect WAV/MP3/FLAC/unknown from magic bytes (filename can't be trusted)."""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if len(data) >= 4 and data[:4] == b"fLaC":
        return "flac"
    if len(data) >= 3 and data[:3] == b"ID3":
        return "mp3"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "mp3"
    return "unknown"


def _miniaudio_ok() -> bool:
    try:
        import miniaudio  # noqa: F401
        return True
    except ImportError:
        return False


@app.get("/api/config")
def api_config() -> Dict[str, Any]:
    lim = get_limits()
    return {
        "max_upload_mb": lim.max_upload_mb,
        "max_upload_bytes": lim.max_upload_mb * 1024 * 1024,
        "max_duration_sec": lim.max_duration_sec,
        "max_duration_min": round(lim.max_duration_sec / 60.0, 1),
        "target_sr": lim.target_sr,
        "window_sec": lim.window_sec,
        "overlap_sec": lim.overlap_sec,
        "mp3_supported": _miniaudio_ok(),
        "processing_version": PROCESSING_VERSION,
        "app_version": app.version,
        "accepted": ["wav", "mp3", "flac"] if _miniaudio_ok() else ["wav"],
    }


@app.post("/api/uploads")
async def api_upload(request: Request,
                     file: UploadFile = File(...),
                     mode: str = Form("piano"),
                     density: str = Form("standard"),
                     title: str = Form("")) -> Dict[str, Any]:
    _rate_or_429(request, "upload", get_settings().rate_uploads_per_min)
    if mode not in ("piano", "arrangement"):
        raise HTTPException(400, "mode must be 'piano' or 'arrangement'")
    if density not in ("simple", "standard", "detailed"):
        raise HTTPException(400, "density must be simple/standard/detailed")
    lim = get_limits()
    data = await file.read()
    if not data:
        raise HTTPException(400, "That file is empty (0 bytes). Pick a recording to continue.")
    if len(data) > lim.max_upload_mb * 1024 * 1024:
        raise HTTPException(400, f"That file is {len(data) / 1e6:.1f} MB, over the "
                                 f"{lim.max_upload_mb} MB limit. Try a shorter clip or "
                                 f"a lower bitrate.")
    kind = sniff_audio_kind(data)
    if kind == "unknown":
        raise HTTPException(400, "We couldn't recognize that file as audio. "
                                 "Please upload an MP3, WAV, or FLAC recording.")
    if kind in ("mp3", "flac") and not _miniaudio_ok():
        raise HTTPException(400, "MP3/FLAC decoding isn't available on this server right now. "
                                 "Please upload a WAV file instead.")
    # Trust content over filename so misnamed files still work.
    ext = {"wav": ".wav", "mp3": ".mp3", "flac": ".flac"}[kind]
    name = f"up_{uuid.uuid4().hex[:12]}{ext}"
    path = os.path.join(audio_dir(), name)
    with open(path, "wb") as f:
        f.write(data)
    job = create_job(mode, path, title or (file.filename or "Untitled"), density)
    log.info("upload %s (%s, %.1fMB) -> %s mode=%s", file.filename, kind,
             len(data) / 1e6, job["id"], mode)
    return {"job_id": job["id"], "status": job["status"]}


SAMPLES = [
    {"id": "synth_melody", "title": "Morning Scale", "file": "synth_melody.wav",
     "blurb": "A simple single-note melody with a repeated note. Best as Piano recording.",
     "mode": "piano"},
    {"id": "synth_chords", "title": "Four-Chord Sketch", "file": "synth_chords.wav",
     "blurb": "Block chords with a top melody. Try both modes and compare.",
     "mode": "piano"},
    {"id": "synth_repeats", "title": "Echoes over Bass", "file": "synth_repeats.wav",
     "blurb": "Repeated high notes over a long bass hold. Tests repeats and sustains.",
     "mode": "piano"},
    {"id": "synth_minute", "title": "One-Minute Band", "file": "synth_minute.wav",
     "blurb": "A minute of chords plus melody. Try Song-or-band arrangement.",
     "mode": "arrangement"},
]

_samples_cache: Optional[List[Dict[str, Any]]] = None


@app.get("/api/samples")
def api_samples() -> Dict[str, Any]:
    """Bundled example recordings for trying the product without uploading."""
    global _samples_cache
    if _samples_cache is None:
        out = []
        for s in SAMPLES:
            path = REPO_ROOT / "fixtures" / s["file"]
            entry = dict(s)
            entry["available"] = path.exists()
            entry["duration_sec"] = None
            if path.exists():
                try:
                    from .audio_ingest import load_audio
                    _, info = load_audio(str(path))
                    entry["duration_sec"] = round(info.duration_sec, 1)
                except Exception:  # noqa: BLE001 - sample probing must not fail the list
                    entry["available"] = False
            out.append(entry)
        _samples_cache = out
    return {"samples": _samples_cache}


class SampleTranscribeBody(BaseModel):
    mode: str = "piano"
    density: str = "standard"


@app.post("/api/samples/{sid}/transcribe")
def api_sample_transcribe(sid: str, body: SampleTranscribeBody) -> Dict[str, Any]:
    if body.mode not in ("piano", "arrangement"):
        raise HTTPException(400, "mode must be 'piano' or 'arrangement'")
    if body.density not in ("simple", "standard", "detailed"):
        raise HTTPException(400, "density must be simple/standard/detailed")
    sample = next((s for s in SAMPLES if s["id"] == sid), None)
    if sample is None:
        raise HTTPException(404, "sample not found")
    path = REPO_ROOT / "fixtures" / sample["file"]
    if not path.exists():
        raise HTTPException(404, "sample audio missing on server")
    job = create_job(body.mode, str(path), f"Example: {sample['title']}", body.density)
    return {"job_id": job["id"], "status": job["status"]}


@app.post("/api/imports")
async def api_import_midi(request: Request,
                          file: UploadFile = File(...),
                          title: str = Form("")) -> Dict[str, Any]:
    """Import a MIDI file as an editable score (MIDI-to-score, no inference)."""
    _rate_or_429(request, "upload", get_settings().rate_uploads_per_min)
    from .midi_import import MidiError, import_midi_file
    data = await file.read()
    if not data:
        raise HTTPException(400, "That file is empty (0 bytes).")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "MIDI files are limited to 10 MB.")
    if data[:4] != b"MThd":
        raise HTTPException(400, "That doesn't look like a MIDI file (expected .mid).")
    name = f"imp_{uuid.uuid4().hex[:12]}.mid"
    path = os.path.join(audio_dir(), name)
    with open(path, "wb") as f:
        f.write(data)
    try:
        score = import_midi_file(path, title or (file.filename or "Imported MIDI"))
    except MidiError as e:
        raise HTTPException(400, str(e))
    from .pipeline import write_artifacts
    pid = f"prj_{uuid.uuid4().hex[:12]}"
    save_project(pid, score.title, "piano", path, score.to_dict())
    write_artifacts(pid, score, {"total_sec": 0, "audio_sec": 0})
    log.info("midi import %s -> %s (%d notes)", file.filename, pid, len(score.notes))
    return {"project_id": pid, "status": "done"}


class RerunBody(BaseModel):
    mode: Optional[str] = None
    density: Optional[str] = None


@app.post("/api/projects/{pid}/rerun")
def api_rerun(pid: str, body: RerunBody) -> Dict[str, Any]:
    """Re-transcribe or re-arrange a project's audio (new score version, one Undo back)."""
    p = get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    if not os.path.exists(p["audio_path"]):
        raise HTTPException(400, "The original audio for this project is gone, so it can't be re-run.")
    mode = body.mode or p["mode"]
    if mode not in ("piano", "arrangement"):
        raise HTTPException(400, "mode must be 'piano' or 'arrangement'")
    old_density = (Score.from_dict(p["score"]).processing or {}).get("density", "standard")
    density = body.density or old_density
    if density not in ("simple", "standard", "detailed"):
        raise HTTPException(400, "density must be simple/standard/detailed")
    job = create_job(mode, p["audio_path"], p["title"], density,
                     params={"retarget": pid})
    return {"job_id": job["id"], "status": job["status"]}


@app.get("/api/jobs")
def api_jobs(request: Request, limit: int = 20) -> Dict[str, Any]:
    _rate_or_429(request, "read", get_settings().rate_requests_per_min)
    return {"jobs": list_jobs(max(1, min(limit, 100)))}


@app.get("/api/jobs/{jid}")
def api_job(request: Request, jid: str) -> Dict[str, Any]:
    _rate_or_429(request, "read", get_settings().rate_requests_per_min)
    job = get_job(jid)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@app.get("/api/projects")
def api_projects(request: Request) -> Dict[str, Any]:
    _rate_or_429(request, "read", get_settings().rate_requests_per_min)
    return {"projects": list_projects()}


@app.get("/api/projects/{pid}")
def api_project(request: Request, pid: str) -> Dict[str, Any]:
    _rate_or_429(request, "read", get_settings().rate_requests_per_min)
    p = get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    score = Score.from_dict(p["score"])
    svg = render_svg(score)
    return {"id": p["id"], "title": p["title"], "mode": p["mode"],
            "audio_url": f"/api/projects/{pid}/audio",
            "created_at": p["created_at"], "updated_at": p["updated_at"],
            "score": score.to_dict(), "svg": svg}


class RenameBody(BaseModel):
    title: str


@app.patch("/api/projects/{pid}")
def api_rename(pid: str, body: RenameBody) -> Dict[str, Any]:
    p = get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    title = (body.title or "").strip()[:120]
    if not title:
        raise HTTPException(400, "title must not be empty")
    score = Score.from_dict(p["score"])
    score.title = title
    saved = save_project(pid, title, p["mode"], p["audio_path"], score.to_dict())
    return {"id": saved["id"], "title": saved["title"]}


@app.delete("/api/projects/{pid}")
def api_delete_project(pid: str) -> Dict[str, Any]:
    p = get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    audio_path = delete_project(pid)
    assert audio_path is not None
    shutil.rmtree(artifact_dir(pid), ignore_errors=True)
    # Only remove audio we own (inside audio_dir); bundled samples stay.
    try:
        owned = os.path.commonpath([os.path.abspath(audio_path), os.path.abspath(audio_dir())])
        is_owned = owned == os.path.abspath(audio_dir())
    except ValueError:
        is_owned = False
    if is_owned and not audio_referenced_elsewhere(audio_path, pid):
        try:
            os.remove(audio_path)
        except OSError:
            pass
    log.info("deleted project %s", pid)
    return {"deleted": pid}


@app.post("/api/projects/{pid}/duplicate")
def api_duplicate(pid: str) -> Dict[str, Any]:
    p = get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    score = Score.from_dict(p["score"])
    score.history = []
    score.redo_stack = []
    score.version = 1
    new_id = f"prj_{uuid.uuid4().hex[:12]}"
    title = (p["title"] + " (copy)")[:120]
    save_project(new_id, title, p["mode"], p["audio_path"], score.to_dict())
    log.info("duplicated project %s -> %s", pid, new_id)
    return {"id": new_id, "title": title}


@app.get("/api/projects/{pid}/audio")
def api_audio(pid: str):
    p = get_project(pid)
    if not p or not os.path.exists(p["audio_path"]):
        raise HTTPException(404, "audio not found")
    ext = os.path.splitext(p["audio_path"])[1].lower()
    mt = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    return FileResponse(p["audio_path"], media_type=mt)


class NoteOp(BaseModel):
    op: str  # update|add|delete|transpose|undo|redo
    id: Optional[str] = None
    ids: Optional[List[str]] = None
    patch: Optional[Dict[str, Any]] = None
    semitones: Optional[int] = None
    label: Optional[str] = None


@app.patch("/api/projects/{pid}/notes")
def api_edit_notes(pid: str, body: NoteOp) -> Dict[str, Any]:
    p = get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    score = Score.from_dict(p["score"])
    if body.op == "update" and body.id and body.patch is not None:
        if not score.update_note(body.id, body.patch, body.label or "edit"):
            raise HTTPException(404, "note not found")
    elif body.op == "add" and body.patch is not None:
        score.add_note(body.patch)
    elif body.op == "delete" and body.ids:
        score.delete_notes(body.ids)
    elif body.op == "transpose" and body.ids and body.semitones is not None:
        if not (-24 <= body.semitones <= 24):
            raise HTTPException(400, "transpose range is ±24 semitones")
        score.transpose(body.ids, body.semitones)
    elif body.op == "undo":
        if not score.undo():
            raise HTTPException(400, "nothing to undo")
    elif body.op == "redo":
        if not score.redo():
            raise HTTPException(400, "nothing to redo")
    else:
        raise HTTPException(400, f"invalid note operation '{body.op}'")
    _persist(pid, p, score)
    return {"score": score.to_dict(), "svg": render_svg(score)}


@app.get("/api/projects/{pid}/export")
def api_export(pid: str, format: str = "mid") -> Response:
    p = get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    score = Score.from_dict(p["score"])
    headers = {"X-Score-Version": str(score.version)}
    if format in ("mid", "midi"):
        return Response(to_midi_bytes(score), media_type="audio/midi",
                        headers={**headers, "Content-Disposition": f'attachment; filename="{pid}.mid"'})
    if format in ("musicxml", "xml"):
        return Response(to_musicxml(score), media_type="application/vnd.recordare.musicxml+xml",
                        headers={**headers, "Content-Disposition": f'attachment; filename="{pid}.musicxml"'})
    if format == "pdf":
        return Response(to_pdf_bytes(score), media_type="application/pdf",
                        headers={**headers, "Content-Disposition": f'attachment; filename="{pid}.pdf"'})
    raise HTTPException(400, "format must be mid, musicxml, or pdf")


class AssistReq(BaseModel):
    text: str
    selection: List[str] = []


@app.post("/api/projects/{pid}/assistant")
def api_assist(pid: str, body: AssistReq) -> Dict[str, Any]:
    p = get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    score = Score.from_dict(p["score"])
    return parse_command(body.text, score, body.selection)


@app.post("/api/projects/{pid}/assistant/apply")
def api_assist_apply(pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    p = get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    score = Score.from_dict(p["score"])
    res = apply_action(score, body.get("action", {}))
    if res.get("ok") and res.get("changed", True):
        _persist(pid, p, score)
        res["score"] = score.to_dict()
        res["svg"] = render_svg(score)
    return res


def _persist(pid: str, p: Dict[str, Any], score: Score) -> None:
    save_project(pid, p["title"], p["mode"], p["audio_path"], score.to_dict())
    ad = artifact_dir(pid)
    with open(os.path.join(ad, "score.json"), "w") as f:
        import json
        json.dump(score.to_dict(), f)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    fp = FRONTEND_DIR / "index.html"
    if not fp.exists():
        return HTMLResponse("<h1>Piano Scribe</h1><p>Frontend not built.</p>", status_code=500)
    return HTMLResponse(fp.read_text())


@app.get("/app.js")
def app_js():
    fp = FRONTEND_DIR / "app.js"
    if not fp.exists():
        raise HTTPException(404, "frontend script missing")
    return FileResponse(str(fp), media_type="application/javascript")


@app.get("/styles.css")
def styles_css():
    fp = FRONTEND_DIR / "styles.css"
    if not fp.exists():
        raise HTTPException(404, "frontend stylesheet missing")
    return FileResponse(str(fp), media_type="text/css")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> JSONResponse:
    """Readiness: storage writable + database readable + worker heartbeat."""
    checks: Dict[str, Any] = {}
    ok = True
    try:
        root = os.path.abspath(storage_root())
        os.makedirs(root, exist_ok=True)
        probe = os.path.join(root, ".writetest")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        list_projects(limit=1)
        checks["storage"] = {"ok": True}
    except Exception as e:  # noqa: BLE001 - readiness must report, not raise
        ok = False
        checks["storage"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    hb = heartbeat_status(get_settings().worker_stale_sec)
    checks["worker"] = {"ok": hb["alive"], **{k: v for k, v in hb.items() if k != "alive"}}
    if not hb["alive"]:
        ok = False
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)
