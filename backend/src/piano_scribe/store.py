"""Durable storage: SQLite jobs/projects + file artifacts on disk."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from .config import storage_root

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  mode TEXT NOT NULL,
  audio_path TEXT NOT NULL,
  title TEXT NOT NULL,
  density TEXT NOT NULL DEFAULT 'standard',
  progress REAL NOT NULL DEFAULT 0,
  stage TEXT NOT NULL DEFAULT 'queued',
  error TEXT DEFAULT '',
  project_id TEXT DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  mode TEXT NOT NULL,
  audio_path TEXT NOT NULL,
  score_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
"""

# Added after 0.1.0; applied idempotently for existing databases.
MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN note_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN duration_sec REAL NOT NULL DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN params TEXT NOT NULL DEFAULT ''",
]


def _db_path() -> str:
    root = os.path.abspath(storage_root())
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "piano_scribe.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(), timeout=30.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    for stmt in MIGRATIONS:
        try:
            c.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    return c


def _now() -> float:
    return time.time()


# -- jobs ------------------------------------------------------------------
def create_job(mode: str, audio_path: str, title: str, density: str = "standard",
               params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = _conn()
    try:
        jid = f"job_{uuid.uuid4().hex[:12]}"
        now = _now()
        c.execute(
            "INSERT INTO jobs (id,status,mode,audio_path,title,density,progress,stage,attempts,params,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,0,'queued',0,?,?,?)",
            (jid, "queued", mode, audio_path, title, density, json.dumps(params or {}), now, now),
        )
        c.commit()
        return get_job(jid)  # type: ignore[return-value]
    finally:
        c.close()


def get_job(jid: str) -> Optional[Dict[str, Any]]:
    c = _conn()
    try:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def update_job(jid: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    c = _conn()
    try:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))
        c.commit()
    finally:
        c.close()


def claim_next_job() -> Optional[Dict[str, Any]]:
    """Atomically claim one queued job for the worker (durable queue)."""
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            c.execute("COMMIT")
            return None
        jid = row["id"]
        c.execute(
            "UPDATE jobs SET status='working', stage='claimed', attempts=attempts+1, updated_at=? WHERE id=?",
            (_now(), jid),
        )
        c.execute("COMMIT")
        return get_job(jid)
    finally:
        c.close()


def recover_stale_jobs(older_than_sec: float, max_attempts: int) -> Dict[str, int]:
    """Requeue jobs stuck in 'working' (crashed worker) or fail them after
    max_attempts. Returns counts. Called by the worker on startup."""
    cutoff = _now() - older_than_sec
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        stuck = c.execute(
            "SELECT id, attempts FROM jobs WHERE status='working' AND updated_at < ?",
            (cutoff,),
        ).fetchall()
        requeued = failed = 0
        for row in stuck:
            if row["attempts"] >= max_attempts:
                c.execute(
                    "UPDATE jobs SET status='failed', stage='failed',"
                    " error='worker stopped responding; giving up after retries',"
                    " updated_at=? WHERE id=?",
                    (_now(), row["id"]),
                )
                failed += 1
            else:
                c.execute(
                    "UPDATE jobs SET status='queued', stage='requeued after worker restart',"
                    " updated_at=? WHERE id=?",
                    (_now(), row["id"]),
                )
                requeued += 1
        c.execute("COMMIT")
        return {"requeued": requeued, "failed": failed}
    finally:
        c.close()


def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# -- projects ---------------------------------------------------------------
def save_project(pid: str, title: str, mode: str, audio_path: str, score: Dict[str, Any]) -> Dict[str, Any]:
    c = _conn()
    try:
        now = _now()
        blob = json.dumps(score)
        notes = score.get("notes", [])
        n_count = len(notes)
        dur = max([float(n.get("end_sec", 0) or 0) for n in notes], default=0.0)
        row = c.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
        if row:
            c.execute(
                "UPDATE projects SET title=?, mode=?, audio_path=?, score_json=?,"
                " note_count=?, duration_sec=?, updated_at=? WHERE id=?",
                (title, mode, audio_path, blob, n_count, dur, now, pid),
            )
        else:
            c.execute(
                "INSERT INTO projects (id,title,mode,audio_path,score_json,note_count,duration_sec,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, title, mode, audio_path, blob, n_count, dur, now, now),
            )
        c.commit()
        return get_project(pid)  # type: ignore[return-value]
    finally:
        c.close()


def get_project(pid: str) -> Optional[Dict[str, Any]]:
    c = _conn()
    try:
        row = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["score"] = json.loads(d.pop("score_json"))
        return d
    finally:
        c.close()


def delete_project(pid: str) -> Optional[str]:
    """Delete a project row. Returns its audio path (caller removes files)."""
    c = _conn()
    try:
        row = c.execute("SELECT audio_path FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
        c.commit()
        return row["audio_path"]
    finally:
        c.close()


def audio_referenced_elsewhere(audio_path: str, except_pid: str) -> bool:
    c = _conn()
    try:
        row = c.execute(
            "SELECT id FROM projects WHERE audio_path=? AND id != ? LIMIT 1",
            (audio_path, except_pid),
        ).fetchone()
        return row is not None
    finally:
        c.close()


def list_projects(limit: int = 100) -> List[Dict[str, Any]]:
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id,title,mode,audio_path,note_count,duration_sec,created_at,updated_at FROM projects"
            " ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def artifact_dir(pid: str) -> str:
    root = os.path.abspath(storage_root())
    d = os.path.join(root, "artifacts", pid)
    os.makedirs(d, exist_ok=True)
    return d


def audio_dir() -> str:
    root = os.path.abspath(storage_root())
    d = os.path.join(root, "audio")
    os.makedirs(d, exist_ok=True)
    return d


# -- worker heartbeat ----------------------------------------------------------
def heartbeat_path() -> str:
    root = os.path.abspath(storage_root())
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "worker.heartbeat")


def record_heartbeat(worker_id: str) -> None:
    try:
        with open(heartbeat_path(), "w") as f:
            json.dump({"worker_id": worker_id, "at": _now()}, f)
    except OSError:
        pass


def heartbeat_status(max_age_sec: float) -> Dict[str, Any]:
    try:
        with open(heartbeat_path()) as f:
            hb = json.load(f)
    except (OSError, ValueError):
        return {"alive": False, "reason": "no heartbeat yet (worker not running?)"}
    age = _now() - float(hb.get("at", 0))
    if age > max_age_sec:
        return {"alive": False, "reason": f"heartbeat stale ({age:.0f}s old)",
                "worker_id": hb.get("worker_id"), "age_sec": round(age, 1)}
    return {"alive": True, "worker_id": hb.get("worker_id"), "age_sec": round(age, 1)}
