"""trade_plan_notification_outbox 리포지토리.

schedule_notifications.py(v1.2 예약알림)와 동일한 패턴 그대로 -- 상태머신
(PENDING/SENDING/SENT/RETRY/FAILED), UNIQUE(plan_id, idempotency_key)로
멱등성 보장. 대상 식별자만 occurrence_id 대신 plan_id+idempotency_key.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from ..utils import utcnow_iso


def enqueue(
    conn: sqlite3.Connection, *, plan_id: str, event_type: str, idempotency_key: str,
    payload: str, channel: str = "telegram",
) -> Optional[dict[str, Any]]:
    """신규 적재면 dict, 이미 있던 이벤트(중복 평가)면 None을 반환한다."""
    now = utcnow_iso()
    cur = conn.execute(
        """
        INSERT INTO trade_plan_notification_outbox
            (plan_id, event_type, idempotency_key, channel, status, payload, attempt_count, next_attempt_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'PENDING', ?, 0, NULL, ?, ?)
        ON CONFLICT(plan_id, idempotency_key) DO NOTHING
        """,
        (plan_id, event_type, idempotency_key, channel, payload, now, now),
    )
    if cur.rowcount == 0:
        return None
    row = conn.execute(
        "SELECT * FROM trade_plan_notification_outbox WHERE plan_id = ? AND idempotency_key = ?",
        (plan_id, idempotency_key),
    ).fetchone()
    return _row_to_dict(row)


def list_pending(conn: sqlite3.Connection, *, now: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
    now = now or utcnow_iso()
    rows = conn.execute(
        "SELECT * FROM trade_plan_notification_outbox "
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
            "UPDATE trade_plan_notification_outbox SET status = ?, next_attempt_at = ?, "
            "attempt_count = attempt_count + 1, updated_at = ? WHERE id = ?",
            (status, next_attempt_at, now, outbox_id),
        )
    else:
        conn.execute(
            "UPDATE trade_plan_notification_outbox SET status = ?, next_attempt_at = ?, updated_at = ? WHERE id = ?",
            (status, next_attempt_at, now, outbox_id),
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "plan_id": row["plan_id"], "event_type": row["event_type"],
        "idempotency_key": row["idempotency_key"], "channel": row["channel"], "status": row["status"],
        "payload": row["payload"], "attempt_count": row["attempt_count"],
        "next_attempt_at": row["next_attempt_at"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
