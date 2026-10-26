"""Runtime configuration. Limits are env-configurable and served verbatim."""
from __future__ import annotations

import os
from dataclasses import dataclass


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


def storage_root() -> str:
    return os.environ.get(
        "PIANO_SCRIBE_STORAGE",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "storage"),
    )
