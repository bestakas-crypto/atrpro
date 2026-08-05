"""schedule_notification_outbox 리포지토리 -- v1.2 스펙(예약알림 텔레그램 발송).

notification_outbox(신호 알림 전용, repositories/notifications.py)와 상태 머신
(PENDING/SENDING/SENT/RETRY/FAILED)·재시도 로직은 동일하되, signal_event_id
대신 occurrence_id로 대상을 식별한다. schema.py의 UNIQUE(occurrence_id,
notification_type, scheduled_date) 제약이 그대로 멱등성(idempotency)을 보장한다 --
같은 회차/같은 알림종류/같은 날짜로 여러 번 enqueue해도 최초 1건만 남는다.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from ..utils import utcnow_iso


def enqueue(
    conn: sqlite3.Connection, *, occurrence_id: str, notification_type: str, scheduled_date: str,
    payload: str, channel: str = "telegram",
) -> Optional[dict[str, Any]]:
    """새로 만들어졌으면(신규 INSERT) dict를 반환하고, 이미 있었으면(중복 요청) None을
    반환한다 -- 호출자가 "이번에 실제로 새로 큐에 넣었는지"를 구분할 수 있게 한다."""
    now = utcnow_iso()
    cur = conn.execute(
        """
        INSERT INTO schedule_notification_outbox
            (occurrence_id, notification_type, scheduled_date, channel, status, payload, attempt_count, next_attempt_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'PENDING', ?, 0, NULL, ?, ?)
        ON CONFLICT(occurrence_id, notification_type, scheduled_date) DO NOTHING
        """,
        (occurrence_id, notification_type, scheduled_date, channel, payload, now, now),
    )
    if cur.rowcount == 0:
        return None
    row = conn.execute(
        "SELECT * FROM schedule_notification_outbox WHERE occurrence_id = ? AND notification_type = ? AND scheduled_date = ?",
        (occurrence_id, notification_type, scheduled_date),
    ).fetchone()
    return _row_to_dict(row)


def list_pending(conn: sqlite3.Connection, *, now: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
    now = now or utcnow_iso()
    rows = conn.execute(
        "SELECT * FROM schedule_notification_outbox "
        "WHERE status = 'PENDING' OR (status = 'RETRY' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)) "
        "ORDER BY created_at ASC LIMIT ?",
        (now, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_status(
    conn: sqlite3.Connection, outbox_id: int, *, status: str, next_attempt_at: Optional[str] = None,
    increment_attempt: bool = False,
) -> None:
    now = utcnow_iso()
    if increment_attempt:
        conn.execute(
            "UPDATE schedule_notification_outbox SET status = ?, next_attempt_at = ?, "
            "attempt_count = attempt_count + 1, updated_at = ? WHERE id = ?",
            (status, next_attempt_at, now, outbox_id),
        )
    else:
        conn.execute(
            "UPDATE schedule_notification_outbox SET status = ?, next_attempt_at = ?, updated_at = ? WHERE id = ?",
            (status, next_attempt_at, now, outbox_id),
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "occurrence_id": row["occurrence_id"],
        "notification_type": row["notification_type"], "scheduled_date": row["scheduled_date"],
        "channel": row["channel"], "status": row["status"], "payload": row["payload"],
        "attempt_count": row["attempt_count"], "next_attempt_at": row["next_attempt_at"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
