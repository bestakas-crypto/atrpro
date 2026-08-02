"""notification_outbox 리포지토리 -- 스펙 12.1/12.2."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..utils import utcnow_iso


def enqueue(
    conn: sqlite3.Connection, *, signal_event_id: int, channel: str, payload: str
) -> dict[str, Any]:
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO notification_outbox
            (signal_event_id, channel, status, payload, attempt_count, next_attempt_at, created_at, updated_at)
        VALUES (?, ?, 'PENDING', ?, 0, NULL, ?, ?)
        ON CONFLICT(signal_event_id, channel) DO NOTHING
        """,
        (signal_event_id, channel, payload, now, now),
    )
    row = conn.execute(
        "SELECT * FROM notification_outbox WHERE signal_event_id = ? AND channel = ?",
        (signal_event_id, channel),
    ).fetchone()
    return _row_to_dict(row)


def list_pending(conn: sqlite3.Connection, *, now: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """발송 대상 조회 -- PENDING은 즉시, RETRY는 next_attempt_at이 지난 것만
    (스펙 12.2 재시도 백오프를 실제로 지키기 위한 필터)."""
    now = now or utcnow_iso()
    rows = conn.execute(
        "SELECT * FROM notification_outbox "
        "WHERE status = 'PENDING' OR (status = 'RETRY' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)) "
        "ORDER BY created_at ASC LIMIT ?",
        (now, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_status(
    conn: sqlite3.Connection, outbox_id: int, *, status: str, next_attempt_at: str | None = None,
    increment_attempt: bool = False,
) -> None:
    now = utcnow_iso()
    if increment_attempt:
        conn.execute(
            "UPDATE notification_outbox SET status = ?, next_attempt_at = ?, "
            "attempt_count = attempt_count + 1, updated_at = ? WHERE id = ?",
            (status, next_attempt_at, now, outbox_id),
        )
    else:
        conn.execute(
            "UPDATE notification_outbox SET status = ?, next_attempt_at = ?, updated_at = ? WHERE id = ?",
            (status, next_attempt_at, now, outbox_id),
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "signal_event_id": row["signal_event_id"],
        "channel": row["channel"],
        "status": row["status"],
        "payload": row["payload"],
        "attempt_count": row["attempt_count"],
        "next_attempt_at": row["next_attempt_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
