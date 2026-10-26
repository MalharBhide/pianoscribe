"""HTTP API. No model inference runs in request handlers.

Uploads are saved + queued; the worker process does audio/ML work.
Exports and SVG are rendered from the saved score version on demand.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import PROCESSING_VERSION
from .assistant import apply_action, parse_command
from .config import get_limits
from .notation import render_svg, to_midi_bytes, to_musicxml, to_pdf_bytes
from .score import Score
from .store import (artifact_dir, audio_dir, create_job, get_job, get_project,
                    list_projects, save_project)

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = REPO_ROOT / "frontend"

app = FastAPI(title="Piano Scribe", version="0.1.0")


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
        "accepted": ["wav", "mp3"] if _miniaudio_ok() else ["wav"],
    }


@app.post("/api/uploads")
async def api_upload(file: UploadFile = File(...),
                     mode: str = Form("piano"),
                     density: str = Form("standard"),
                     title: str = Form("")) -> Dict[str, Any]:
    if mode not in ("piano", "arrangement"):
        raise HTTPException(400, "mode must be 'piano' or 'arrangement'")
    if density not in ("simple", "standard", "detailed"):
        raise HTTPException(400, "density must be simple/standard/detailed")
    lim = get_limits()
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file (0 bytes)")
    if len(data) > lim.max_upload_mb * 1024 * 1024:
        raise HTTPException(400, f"file is {len(data) / 1e6:.1f} MB, over the configured limit of "
                                 f"{lim.max_upload_mb} MB")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".wav", ".wave", ".mp3"):
        raise HTTPException(400, f"unsupported file type '{ext or '(none)'}': upload MP3 or WAV")
    if ext == ".mp3" and not _miniaudio_ok():
        raise HTTPException(400, "MP3 decoding is unavailable (miniaudio not installed); upload WAV")
    name = f"up_{uuid.uuid4().hex[:12]}{ext}"
    path = os.path.join(audio_dir(), name)
    with open(path, "wb") as f:
        f.write(data)
    job = create_job(mode, path, title or (file.filename or "Untitled"), density)
    return {"job_id": job["id"], "status": job["status"]}


@app.get("/api/jobs/{jid}")
def api_job(jid: str) -> Dict[str, Any]:
    job = get_job(jid)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@app.get("/api/projects")
def api_projects() -> Dict[str, Any]:
    return {"projects": list_projects()}


@app.get("/api/projects/{pid}")
def api_project(pid: str) -> Dict[str, Any]:
    p = get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    score = Score.from_dict(p["score"])
    svg = render_svg(score)
    return {"id": p["id"], "title": p["title"], "mode": p["mode"],
            "audio_url": f"/api/projects/{pid}/audio",
            "created_at": p["created_at"], "updated_at": p["updated_at"],
            "score": score.to_dict(), "svg": svg}


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
    if res.get("ok"):
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


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}
