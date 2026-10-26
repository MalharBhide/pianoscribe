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


def _db_path() -> str:
    root = os.path.abspath(storage_root())
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "piano_scribe.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _now() -> float:
    return time.time()


# -- jobs ------------------------------------------------------------------
def create_job(mode: str, audio_path: str, title: str, density: str = "standard") -> Dict[str, Any]:
    c = _conn()
    try:
        jid = f"job_{uuid.uuid4().hex[:12]}"
        now = _now()
        c.execute(
            "INSERT INTO jobs (id,status,mode,audio_path,title,density,progress,stage,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,0,'queued',?,?)",
            (jid, "queued", mode, audio_path, title, density, now, now),
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
            "UPDATE jobs SET status='working', stage='claimed', updated_at=? WHERE id=?",
            (_now(), jid),
        )
        c.execute("COMMIT")
        return get_job(jid)
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
        row = c.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
        if row:
            c.execute(
                "UPDATE projects SET title=?, mode=?, audio_path=?, score_json=?, updated_at=? WHERE id=?",
                (title, mode, audio_path, blob, now, pid),
            )
        else:
            c.execute(
                "INSERT INTO projects (id,title,mode,audio_path,score_json,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (pid, title, mode, audio_path, blob, now, now),
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


def list_projects(limit: int = 50) -> List[Dict[str, Any]]:
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id,title,mode,audio_path,created_at,updated_at FROM projects"
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
