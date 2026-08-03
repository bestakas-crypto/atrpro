"""analysis_results 리포지토리 -- LLM 매크로 브리핑 결과 저장/조회. 2026-08-03 추가."""
from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from ..utils import utcnow_iso


def save_result(
    conn: sqlite3.Connection, *, snapshot_json: str, result_text: str, provider: str, model: str,
) -> dict[str, Any]:
    result_id = uuid.uuid4().hex
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO analysis_results (id, created_at, snapshot_json, result_text, provider, model)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (result_id, now, snapshot_json, result_text, provider, model),
    )
    return get_result(conn, result_id)  # type: ignore[return-value]


def get_result(conn: sqlite3.Connection, result_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM analysis_results WHERE id = ?", (result_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def get_latest_result(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM analysis_results ORDER BY created_at DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return dict(row)
