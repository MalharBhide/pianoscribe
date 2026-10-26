"""Runtime configuration. Limits are env-configurable and served verbatim."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _str(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Limits:
    max_upload_mb: int = 50
    max_duration_sec: float = 1800.0  # 30 minutes
    max_worker_sec: float = 3600.0
    target_sr: int = 22050
    window_sec: float = 30.0
    overlap_sec: float = 5.0


def get_limits() -> Limits:
    return Limits(
        max_upload_mb=_int("PIANO_SCRIBE_MAX_UPLOAD_MB", 50),
        max_duration_sec=_float("PIANO_SCRIBE_MAX_DURATION_SEC", 1800.0),
        max_worker_sec=_float("PIANO_SCRIBE_MAX_WORKER_SEC", 3600.0),
        target_sr=_int("PIANO_SCRIBE_TARGET_SR", 22050),
        window_sec=_float("PIANO_SCRIBE_WINDOW_SEC", 30.0),
        overlap_sec=_float("PIANO_SCRIBE_OVERLAP_SEC", 5.0),
    )


@dataclass(frozen=True)
class Settings:
    log_level: str = "INFO"
    cors_origins: Tuple[str, ...] = ()
    rate_uploads_per_min: int = 60
    rate_requests_per_min: int = 600
    job_timeout_sec: float = 3600.0
    job_max_attempts: int = 3
    worker_poll_sec: float = 2.0
    worker_stale_sec: float = 120.0
    request_body_log: bool = False


def get_settings() -> Settings:
    origins = tuple(
        o.strip() for o in _str("PIANO_SCRIBE_CORS_ORIGINS", "").split(",") if o.strip()
    )
    return Settings(
        log_level=_str("PIANO_SCRIBE_LOG_LEVEL", "INFO").upper(),
        cors_origins=origins,
        rate_uploads_per_min=_int("PIANO_SCRIBE_RATE_UPLOADS_PER_MIN", 60),
        rate_requests_per_min=_int("PIANO_SCRIBE_RATE_REQUESTS_PER_MIN", 600),
        job_timeout_sec=_float("PIANO_SCRIBE_JOB_TIMEOUT_SEC", 3600.0),
        job_max_attempts=max(1, _int("PIANO_SCRIBE_JOB_MAX_ATTEMPTS", 3)),
        worker_poll_sec=_float("PIANO_SCRIBE_WORKER_POLL_SEC", 2.0),
        worker_stale_sec=_float("PIANO_SCRIBE_WORKER_STALE_SEC", 120.0),
    )


def storage_root() -> str:
    return os.environ.get(
        "PIANO_SCRIBE_STORAGE",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "storage"),
    )
