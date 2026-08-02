"""공용 유틸리티 -- ID 생성, 시각 포맷."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
